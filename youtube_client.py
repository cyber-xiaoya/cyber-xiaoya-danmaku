#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""youtube_client.py — YouTube 直播弹幕采集(免 API key).

为什么不用官方 Data API:
  官方 liveChatMessages.list 要走 Google Cloud 建项目 + API key, **每分钟配额**并且
  每次 list 记 5 个配额单位 —— 一整天轮询分分钟把 10,000/天 打光。而网页自己用的是
  InnerTube 接口, 不需要 key、不扣配额, 只要能打开直播间网页就能读。

链路(2026-10-21 实测通过, 参照 pytchat/chat-downloader 的做法):
  1) GET https://www.youtube.com/live_chat?v=<videoId>&is_popout=1
       ⚠ watch 页里挖不到 liveChatRenderer(聊天是懒加载的), 必须走这个独立页;
       ⚠ 变量是 window["ytInitialData"] = {...}, 不是裸的 ytInitialData = {...}
       → 里面 liveChatRenderer.continuations[0].reloadContinuationData.continuation 就是起点
       → 顺带抓 INNERTUBE_API_KEY / INNERTUBE_CLIENT_VERSION 塞进请求上下文
  2) 循环 POST https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key=...
       body = {"context":{"client":{"clientName":"WEB","clientVersion":...}},
               "continuation": <token>}
       → actions[] 里是 addChatItemAction / addLiveChatTickerItemAction
       → 下一轮 token 在 continuationContents.liveChatContinuation.continuations[0]，带 timeoutMs
  3) 直播结束 / 聊天被关 时 continuations 消失 → 隔 retry_sec 重新 seed(主播重开播也能自动接上)

支持输入的写法(都行):
  纯 videoId:   dQw4w9WgXcQ
  观看页:       https://www.youtube.com/watch?v=dQw4w9WgXcQ
  直播页:       https://www.youtube.com/live/dQw4w9WgXcQ
  短链:         https://youtu.be/dQw4w9WgXcQ
  后台(Studio): https://studio.youtube.com/video/dQw4w9WgXcQ/livestreaming?c=UC...

映射到阅读器的事件:
  liveChatTextMessageRenderer                       → Chat        {name, content}
  liveChatPaidMessageRenderer     (Super Chat)      → Gift        {name, item:"SuperChat $5", num:1, price_yuan, content}
  liveChatPaidStickerRenderer     (Super Sticker)   → Gift        {name, item:"SuperSticker $2", num:1, price_yuan}
  liveChatMembershipItemRenderer  (新会员/里程碑)    → Subscribe   {name, item:"频道会员", num:1}  (带留言的里程碑额外发 Chat)
  liveChatSponsorshipsGiftPurchaseAnnouncement      → Gift        {name, item:"频道会员(赠送)", num:N}
  liveChatViewerEngagementMessageRenderer           → 忽略(系统提示)
  liveChatModeChangeMessageRenderer                 → 忽略(慢速/订阅者模式切换, 只记一行日志)
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# 第一次进 youtube.com 会被拦 consent 页, 带这个 cookie 直接放行
COOKIE = "CONSENT=YES+1; SOCS=CAI"
FALLBACK_CLIENT_VERSION = "2.20241010.00.00"

_CUR_MAP = (("$", 7.1), ("€", 7.8), ("£", 9.0), ("¥", 1.0), ("￥", 1.0))


def _is_channel_like(s: str) -> bool:
    """频道写法: @handle / UCxxxxxxxx(24位频道id) / .../@handle / .../channel/UCxxx。"""
    t = (s or "").strip()
    if not t:
        return False
    if t.startswith("@") or re.fullmatch(r"UC[A-Za-z0-9_-]{22}", t):
        return True
    if "/@" in t or "/channel/" in t:
        return True
    return False


def _is_video_like(s: str) -> bool:
    """明确的"这一场直播"写法: 11 位 id / watch?v= / /live/<id> / youtu.be / Studio 后台。"""
    t = (s or "").strip()
    if not t:
        return False
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", t):
        return True
    if "watch?v=" in t or "youtu.be/" in t or "/video/" in t:
        return True
    if re.search(r"/(?:live|embed|shorts)/[A-Za-z0-9_-]{11}", t):
        return True
    return False


def video_id_of(target: str) -> str:
    """把各种写法的 YouTube 链接/ID 归一成 11 位 videoId。"""
    s = (target or "").strip().strip('"\'')
    if not s:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    q = urllib.parse.urlparse(s if "//" in s else "https://" + s)
    host = (q.netloc or "").lower()
    if "youtu.be" in host:
        return q.path.lstrip("/").split("/")[0][:11]
    v = urllib.parse.parse_qs(q.query).get("v")
    if v and v[0]:
        return v[0][:11]
    m = re.search(r"/(?:live|embed|shorts|video)/([A-Za-z0-9_-]{11})", q.path or "")
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z0-9_-]{11})", s)      # 最后兜底: 从整串里挑 11 位
    return m.group(1) if m else s


def _runs_text(obj) -> str:
    """把 {runs:[{text}...]} / {simpleText} 统一成字符串。"""
    if not isinstance(obj, dict):
        return ""
    if obj.get("simpleText") is not None:
        return str(obj.get("simpleText") or "")
    return "".join(str(r.get("text") or "") for r in (obj.get("runs") or []) if isinstance(r, dict))


def _money_yuan(text: str) -> float:
    """"$5.00" / "¥30.00" / "€2.50" → 人民币元(粗略汇率, 只用于 VIP 门槛判断)。"""
    t = (text or "").strip()
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)", t)
    if not m:
        return 0.0
    try:
        num = float(m.group(1).replace(",", ""))
    except Exception:
        return 0.0
    rate = 1.0
    for sym, r in _CUR_MAP:
        if sym in t:
            rate = r
            break
    return round(num * rate, 2)


class YouTubeClient(threading.Thread):
    """YouTube 直播弹幕采集线程。emit(ev) 由 reader 提供, 线程安全由 reader 的队列保证。"""

    def __init__(self, target: str, emit, cfg: dict | None = None):
        self.cfg = cfg or {}
        raw = str(target or "").strip()
        chan = str(self.cfg.get("channel") or "").strip()
        # 顺序很重要: 先认"这一场直播"的写法(Studio 链接的 query 里也带 UC…), 再认频道
        if chan:
            self.channel, self.video_id = chan, ""
        elif _is_video_like(raw):
            self.channel, self.video_id = "", video_id_of(raw)
        elif _is_channel_like(raw):
            self.channel, self.video_id = raw, ""
        else:
            self.channel, self.video_id = "", video_id_of(raw)
        super().__init__(name=f"yt-{self.video_id or self.channel or 'x'}", daemon=True)
        self.target = raw
        self.emit = emit
        self.stop = threading.Event()
        self.debug = bool(self.cfg.get("debug"))
        self.retry_sec = max(5, int(self.cfg.get("retry_sec", 20) or 20))
        self.discover_sec = max(15, int(self.cfg.get("discover_sec", 45) or 45))
        self.poll_ms = max(0, int(self.cfg.get("poll_ms", 0) or 0))   # 0 = 用服务端建议值
        self.sess = requests.Session()
        self.sess.headers.update({
            "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
            "Cookie": str(self.cfg.get("cookie") or COOKIE),
        })
        self._seen: dict[str, float] = {}      # 消息去重(messageId → 时间)
        self._err_streak = 0

    # ───────────────────────────── 日志 ─────────────────────────────

    def _log(self, msg: str, green=False, yellow=False, gray=False, err=False):
        from logutil import platform_log
        platform_log("YouTube", msg, green=green, yellow=yellow, gray=gray, err=err)

    # ───────────────────────────── 取 seed ─────────────────────────────

    def _seed(self) -> tuple[str, str, str]:
        """返回 (continuation, api_key, client_version)。失败抛异常。"""
        url = f"https://www.youtube.com/live_chat?v={self.video_id}&is_popout=1"
        r = self.sess.get(url, timeout=25)
        if r.status_code != 200:
            raise RuntimeError(f"chat 页 HTTP {r.status_code}")
        html = r.text
        m = re.search(r'window\["ytInitialData"\]\s*=\s*(\{.+?\});</script>', html, re.S)
        if not m:
            m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", html, re.S)
        if not m:
            raise RuntimeError("chat 页里没找到 ytInitialData")
        data = json.loads(m.group(1))
        if re.search(r"Chat is disabled for this live stream", html):
            raise RuntimeError("这场直播的聊天室是关闭的(Chat is disabled)")
        lcr = self._find_livechat(data)
        if not lcr:
            raise RuntimeError("没挖到 liveChatRenderer(直播未开始/已结束/需登录)")
        cont = ""
        for c in lcr.get("continuations") or []:
            for k in ("reloadContinuationData", "invalidationContinuationData", "timedContinuationData"):
                if isinstance(c.get(k), dict) and c[k].get("continuation"):
                    cont = c[k]["continuation"]
                    break
            if cont:
                break
        if not cont:
            raise RuntimeError("liveChatRenderer 里没有 continuation")
        key = (re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html) or [None, ""])[1]
        cv = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
        return cont, (key or ""), (cv.group(1) if cv else FALLBACK_CLIENT_VERSION)

    @staticmethod
    def _find_livechat(data: dict) -> dict | None:
        out: list[dict] = []

        def walk(node):
            if isinstance(node, dict):
                if "liveChatRenderer" in node and isinstance(node["liveChatRenderer"], dict):
                    out.append(node["liveChatRenderer"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
        return out[0] if out else None

    # ───────────────────────── 频道 → 当前直播 ─────────────────────────

    def _channel_live_url(self) -> str:
        """把频道写法拼成 <频道>/live。"""
        ch = self.channel.strip()
        if ch.startswith("http://") or ch.startswith("https://"):
            base = ch.rstrip("/")
            if base.endswith("/live"):
                return base
            if "/watch?" in base:                 # 误给了一条视频链接 → 直接当直播看
                return base
            return base + "/live"
        if ch.startswith("@"):
            return f"https://www.youtube.com/{ch}/live"
        return f"https://www.youtube.com/channel/{ch}/live"

    def _player_is_live(self, vid: str, key: str, cv: str) -> bool:
        """用 InnerTube player 复核"这条真的是正在直播" —— /live 页里第一个 videoId 可能是回放,
        只看 HTML 里的 isLiveNow 标记会误判, 必须问 player 的 videoDetails.isLive。"""
        url = "https://www.youtube.com/youtubei/v1/player"
        if key:
            url += "?key=" + key
        try:
            r = self.sess.post(url, json={"context": {"client": {
                "clientName": "WEB", "clientVersion": cv, "hl": "en", "gl": "US"}},
                "videoId": vid}, timeout=20,
                headers={"Content-Type": "application/json",
                         "Origin": "https://www.youtube.com"})
            if r.status_code != 200:
                return False
            vd = (r.json().get("videoDetails") or {})
            return bool(vd.get("isLive")) or bool(vd.get("isLiveContent"))
        except Exception:                          # noqa: BLE001
            return False

    def resolve_live(self) -> str:
        """频道 → 当前直播 videoId; 没在播返回 ""。"""
        url = self._channel_live_url()
        r = self.sess.get(url, timeout=25, allow_redirects=True)
        if r.status_code != 200:
            raise RuntimeError(f"频道页 HTTP {r.status_code}")
        html = r.text
        key = (re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html) or [None, ""])[1]
        cv_m = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
        cv = cv_m.group(1) if cv_m else FALLBACK_CLIENT_VERSION
        m = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", r.url)
        cand = m.group(1) if m else ""
        if not cand:
            # 直播标记前最近的那条 videoId —— /live 页把"正在直播"的那条排在最前面
            pos = html.find('"isLiveNow":true')
            if pos < 0:
                pos = html.find('"isLive":true')
            if pos > 0:
                ids = list(re.finditer(r'"videoId":"([A-Za-z0-9_-]{11})"', html[:pos]))
                cand = ids[-1].group(1) if ids else ""
        if not cand:
            return ""
        if not self._player_is_live(cand, key or "", cv):
            return ""
        return cand

    def _discover(self) -> str:
        try:
            vid = self.resolve_live()
        except Exception as e:                      # noqa: BLE001
            self._log(f"找直播失败: {type(e).__name__}: {e} · {self.discover_sec}s 后再找",
                      yellow=True)
            return ""
        if not vid:
            self._log(f"频道没在直播 · {self.discover_sec}s 后再看", gray=True)
            return ""
        self._log(f"频道当前直播 → {vid}", green=True)
        return vid

    # ───────────────────────────── 主循环 ─────────────────────────────

    def run(self):
        if self.channel:
            self._log(f"按频道找直播: {self.channel}", gray=True)
        else:
            self._log(f"目标 https://www.youtube.com/watch?v={self.video_id} · 开始拉弹幕", gray=True)
        while not self.stop.is_set():
            if not self.video_id:                     # 频道模式: 先找当前这场
                vid = self._discover()
                if not vid:
                    if self.stop.wait(self.discover_sec):
                        return
                    continue
                self.video_id = vid
            try:
                cont, key, cv = self._seed()
            except Exception as e:                      # noqa: BLE001
                self._log(f"连接失败: {e} · {self.retry_sec}s 后重试", yellow=True)
                if self.stop.wait(self.retry_sec):
                    return
                continue
            self._err_streak = 0
            self._log(f"已连上聊天流(免 key) · video={self.video_id} · clientVersion={cv}", green=True)
            try:
                self._poll_loop(cont, key, cv)
            except Exception as e:                      # noqa: BLE001
                self._log(f"会话异常: {type(e).__name__}: {e}", err=True)
            if self.stop.is_set():
                return
            if self.channel:
                # 频道模式: 这场结束了就回到"找下一场", 主播重开播能自动接上
                self.video_id = ""
                if self.stop.wait(5):
                    return
                continue
            self._log(f"{self.retry_sec}s 后重连…", gray=True)
            if self.stop.wait(self.retry_sec):
                return

    def _poll_loop(self, cont: str, key: str, cv: str):
        url = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
        if key:
            url += "?key=" + key
        ctx = {"context": {"client": {"clientName": "WEB", "clientVersion": cv,
                                      "hl": "en", "gl": "US"}}}
        headers = {"Content-Type": "application/json",
                   "Origin": "https://www.youtube.com",
                   "Referer": f"https://www.youtube.com/watch?v={self.video_id}"}
        while not self.stop.is_set():
            try:
                r = self.sess.post(url, json=dict(ctx, continuation=cont),
                                   headers=headers, timeout=20)
            except Exception as e:                      # noqa: BLE001
                self._err_streak += 1
                wait = min(60, 3 * self._err_streak)
                self._log(f"轮询异常({type(e).__name__}) · {wait}s 后重试", yellow=True)
                if self.stop.wait(wait):
                    return
                continue
            if r.status_code != 200:
                self._err_streak += 1
                wait = min(60, 3 * self._err_streak)
                self._log(f"轮询 HTTP {r.status_code} · {wait}s 后重试", yellow=True)
                if self.stop.wait(wait):
                    return
                continue
            self._err_streak = 0
            try:
                j = r.json()
            except Exception:                           # noqa: BLE001
                self._log("响应不是 JSON, 重新 seed", yellow=True)
                return
            cc = j.get("continuationContents") or {}
            lc = cc.get("liveChatContinuation") or {}
            if not lc and isinstance(j.get("actions"), list):   # 少数响应把 actions 放顶层
                lc = {"actions": j.get("actions"), "continuations": j.get("continuations") or []}
            actions = lc.get("actions") or []
            for a in actions:
                self._on_action(a)
            # 下一轮 token
            nxt, wait_ms = None, 3000
            conts = (lc.get("continuations") if isinstance(lc, dict) else None) or []
            for c in conts:
                for k in ("invalidationContinuationData", "timedContinuationData",
                          "reloadContinuationData", "liveChatReplayContinuationData"):
                    d = c.get(k) if isinstance(c, dict) else None
                    if isinstance(d, dict) and d.get("continuation"):
                        nxt = d["continuation"]
                        wait_ms = int(d.get("timeoutMs") or d.get("timeoutDurationMs") or 3000)
                        break
                if nxt:
                    break
            if not nxt:
                self._log("聊天流结束(直播下播/聊天被关) —— 等主播重开播", yellow=True)
                return
            cont = nxt
            secs = self.poll_ms / 1000.0 if self.poll_ms else wait_ms / 1000.0
            if self.stop.wait(max(1.0, min(15.0, secs))):
                return

    # ───────────────────────────── 事件解析 ─────────────────────────────

    def _on_action(self, action: dict):
        item = (action.get("addChatItemAction") or {}).get("item") or {}
        if not isinstance(item, dict) or not item:
            return
        for kind, p in item.items():
            if not isinstance(p, dict):
                continue
            try:
                self._dispatch(kind, p)
            except Exception as e:                      # noqa: BLE001
                if self.debug:
                    self._log(f"[{kind}] 解析失败: {e}", err=True)
            return                                       # 一条 item 只会是其中一种

    def _dedupe(self, mid: str) -> bool:
        """True = 这条见过(重复), 应跳过。"""
        now = time.time()
        if len(self._seen) > 4000:
            self._seen = {k: v for k, v in self._seen.items() if now - v < 600}
        if mid and mid in self._seen:
            return True
        if mid:
            self._seen[mid] = now
        return False

    def _dispatch(self, kind: str, p: dict):
        name = _runs_text(p.get("authorName") or {}).strip()
        mid = str(p.get("id") or "")
        if kind == "liveChatTextMessageRenderer":
            if self._dedupe(mid):
                return
            text = _runs_text(p.get("message") or {}).strip()
            if name and text:
                self.emit({"platform": "YouTube", "type": "Chat",
                           "data": {"name": name, "content": text}})
            return
        if kind in ("liveChatPaidMessageRenderer", "liveChatPaidStickerRenderer"):
            if self._dedupe(mid):
                return
            amount = _runs_text(p.get("purchaseAmountText") or {}) or _runs_text(p.get("sticker") or {})
            text = _runs_text(p.get("message") or {}).strip()
            label = ("SuperChat" if kind == "liveChatPaidMessageRenderer" else "SuperSticker")
            if amount:
                label += " " + amount
            if name:
                self.emit({"platform": "YouTube", "type": "Gift",
                           "data": {"name": name, "item": label, "num": 1,
                                    "price_yuan": _money_yuan(amount),
                                    "content": text}})
            return
        if kind == "liveChatMembershipItemRenderer":
            if self._dedupe(mid):
                return
            head = _runs_text(p.get("headerPrimaryText") or {}) or _runs_text(p.get("headerSubtext") or {})
            text = _runs_text(p.get("message") or {}).strip()
            item_name = "频道会员"
            if "milestone" in json.dumps(p, ensure_ascii=False)[:400].lower():
                item_name = "会员里程碑"
            if name:
                self.emit({"platform": "YouTube", "type": "Subscribe",
                           "data": {"name": name, "item": item_name, "num": 1}})
                if text:                                  # 里程碑常带一句留言 → 当弹幕再发一条
                    self.emit({"platform": "YouTube", "type": "Chat",
                               "data": {"name": name, "content": text}})
            return
        if kind.startswith("liveChatSponsorshipsGiftPurchaseAnnouncement"):
            if self._dedupe(mid):
                return
            raw = json.dumps(p, ensure_ascii=False)
            m = re.search(r'"(\d+)\s*(?:Gift Memberships|individual memberships|memberships)', raw, re.I)
            num = int(m.group(1)) if m else 1
            if name:
                self.emit({"platform": "YouTube", "type": "Gift",
                           "data": {"name": name, "item": f"频道会员(赠送×{num})", "num": num}})
            return
        if kind == "liveChatSponsorshipsGiftRedemptionAnnouncementRenderer":
            if self._dedupe(mid):
                return
            if name:
                self.emit({"platform": "YouTube", "type": "Subscribe",
                           "data": {"name": name, "item": "收到会员", "num": 1}})
            return
        if kind == "liveChatModeChangeMessageRenderer":
            msg = _runs_text(p.get("message") or {}) or _runs_text(p.get("text") or {})
            self._log(f"聊天模式变更: {msg[:60]}", gray=True)
            return
        if kind == "liveChatViewerEngagementMessageRenderer":
            if self.debug:
                self._log(f"系统提示: {_runs_text(p.get('message') or {})[:60]}", gray=True)
            return
        if self.debug:
            self._log(f"未处理的消息类型: {kind}", gray=True)
