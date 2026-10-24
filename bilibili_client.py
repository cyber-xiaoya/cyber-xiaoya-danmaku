#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""bilibili_client.py — B站直播弹幕采集(官方 web WS 协议, 零第三方依赖)。

链路:
  room_init 解析真实房间号 → getDanmuInfo 拿 token
  → wss://{host}/sub 握手 → op=7 鉴权(protover=2, zlib)
  → op=5 命令流 → 归一化事件 emit({platform, type, data})

说明:
  - B站弹幕/礼物/进场/关注/点赞/舰长/SC 对未登录访客可见, 无需 Cookie;
    访问 www.bilibili.com 领 buvid3 以降低风控(-352)概率。
  - 协议参考社区整理 bilibili-API-collect, protover=2 走 zlib(标准库自带)。
"""

import base64
import hashlib
import hmac
import json
import re
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
import zlib

from logutil import dim_log
from wsclient import WSClient, WSClosed

OP_HEARTBEAT = 2      # 客户端心跳
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5        # 命令消息
OP_AUTH = 7
OP_AUTH_REPLY = 8

HEADER_LEN = 16

GUARD_NAMES = {1: "总督", 2: "提督", 3: "舰长"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ── WBI 签名(bilibili-API-collect 存档文档; 该仓库已因律师函关停, 算法为公开常识) ──
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
_BOOT_LOCK = threading.Lock()
_BOOT: dict = {}  # {until, ticket, mixin}

# 登录态缓存: {until, mid}。mid=0 表示 cookie 未登录(SESSDATA 过期/被踢/风控)。
# ⚠ 2026-10-21 实测: getDanmuInfo 下发的 token 与 auth 里的 uid 必须同源。
#    SESSDATA 失效后服务端按游客发 token, 而 client 仍用 cookie 里的 DedeUserID 当 uid
#    → 服务端立刻关闭连接(日志表现为「已连接…等待鉴权」紧跟「连接被关闭」的死循环)。
#    所以 uid 一定要问 nav: 登录有效用 mid, 否则必须是 0。
_NAV_LOCK = threading.Lock()
_NAV: dict = {}   # {until, mid}


def _wbi_sign(params: dict, mixin_key: str) -> dict:
    """按 WBI 算法对参数签名, 返回含 wts/w_rid 的新参数 dict。"""
    p = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in params.items()}
    p["wts"] = str(int(time.time()))
    qs = urllib.parse.urlencode(sorted(p.items()), quote_via=urllib.parse.quote)
    p["w_rid"] = hashlib.md5((qs + mixin_key).encode("utf-8")).hexdigest()
    return p


# ── 极简 protobuf 线格式解析(只够解 INTERACT_WORD_V2 的 pb 字段) ──

def _varint(data: bytes, i: int):
    res = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        res |= (b & 0x7F) << shift
        if not b & 0x80:
            return res, i
        shift += 7


def _pb_fields(data: bytes):
    """yield (field_no, wire_type, value); value: varint 时为 int, LEN 时为 bytes。"""
    i, n = 0, len(data)
    while i < n:
        try:
            tag, i = _varint(data, i)
        except IndexError:
            return
        field, wt = tag >> 3, tag & 7
        try:
            if wt == 0:
                v, i = _varint(data, i)
            elif wt == 2:
                ln, i = _varint(data, i)
                v = data[i:i + ln]
                i += ln
            elif wt == 5:
                v, i = data[i:i + 4], i + 4
            elif wt == 1:
                v, i = data[i:i + 8], i + 8
            else:
                return
        except IndexError:
            return
        yield field, wt, v


def _parse_gift_v2(pb_b64: str) -> str:
    """从 SEND_GIFT_V2 的 pb(base64) 提取发送者昵称。

    实测布局: 1=连击id(varint), 2=发送者昵称(LEN), 3=头像URL(LEN),
    10=礼物详情子消息(LEN, 含礼物名/数量/价格, 简化朗读不解析)。
    """
    try:
        raw = base64.b64decode(pb_b64 + "=" * (-len(pb_b64) % 4))
    except Exception:
        return ""
    for f, wt, v in _pb_fields(raw):
        if f == 2 and wt == 2:
            return v.decode("utf-8", "replace").rstrip("*＊")
    return ""


def _parse_interact_v2(pb_b64: str) -> tuple[str, int, int]:
    """从 INTERACT_WORD_V2 的 pb(base64) 提取 (昵称, msg_type, room_id)。

    实测布局: 2=显示昵称(LEN, 可能带隐私打码 ***), 4=交互标记(LEN, 不用),
    5=msg_type(varint, 1=进场 2=关注), 6=来源房间号(varint),
    9=用户信息(LEN 子消息, 其 field3=未打码昵称, field1=uid)。
    隐私打码时优先取 field9.3, 都没有则剥掉尾部 ***。
    """
    uname, real, msg_type, room = "", "", 0, 0
    try:
        raw = base64.b64decode(pb_b64 + "=" * (-len(pb_b64) % 4))
    except Exception:
        return uname, msg_type, room
    for f, wt, v in _pb_fields(raw):
        if f == 2 and wt == 2:
            uname = v.decode("utf-8", "replace")
        elif f == 5 and wt == 0:
            msg_type = int(v)
        elif f == 6 and wt == 0:
            room = int(v)
        elif f == 9 and wt == 2:
            for f2, wt2, v2 in _pb_fields(v):
                if f2 == 3 and wt2 == 2:
                    real = v2.decode("utf-8", "replace")
                    break
    name = real if (real and "*" in uname) else uname
    if not name:
        name = real
    name = name.rstrip("*＊")
    return name, msg_type, room


def _http_json(url: str, opener: urllib.request.OpenerDirector, timeout: float = 10.0,
               headers: dict | None = None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://live.bilibili.com/",
        "Origin": "https://live.bilibili.com",
        **(headers or {}),
    })
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _pack(op: int, body: bytes, protover: int = 1, seq: int = 1) -> bytes:
    return struct.pack(">IHHII", len(body) + HEADER_LEN, HEADER_LEN, protover, op, seq) + body


class BilibiliClient(threading.Thread):
    """B站弹幕采集线程。emit(ev) 由 reader 提供, 线程安全由 queue 保证。"""

    def __init__(self, room_id: str, emit, cfg: dict | None = None):
        super().__init__(name=f"bili-{room_id}", daemon=True)
        self.room_input = str(room_id).strip()
        self.emit = emit
        self.cfg = cfg or {}
        self.stop = threading.Event()
        self.debug = bool(self.cfg.get("debug"))
        self.combo_wait = max(0.3, self.cfg.get("combo_wait_ms", 1500) / 1000.0)
        self.retry_sec = max(5, self.cfg.get("retry_sec", 15))

        self.opener: urllib.request.OpenerDirector | None = None
        self.jar = None
        self.user_cookie = str(self.cfg.get("cookie") or "").strip()
        self.room_id = 0          # 真实房间号
        self.live_status = -1
        self._offline_notified = False
        self._agg: dict = {}      # (uid,giftId) -> dict(num=..., last=...)
        self._agg_lock = threading.Lock()
        self._unknown: dict = {}

    # ────────────────────────── HTTP 准备 ──────────────────────────

    def _make_opener(self):
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        self.jar = jar
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.plain_opener = urllib.request.build_opener()  # 无 cookie 处理器(手动 Cookie 头)
        try:  # 领 buvid3 / b_nut
            self.opener.open(urllib.request.Request(
                "https://www.bilibili.com/", headers={"User-Agent": UA}), timeout=10).read(1024)
        except Exception:
            pass

    def _cookie_header(self, with_ticket: bool = True) -> str:
        """手动 Cookie 头: 优先用户登录 cookie, 否则用游客 jar(buvid3)。"""
        parts: list = []
        if self.user_cookie:
            parts.append(self.user_cookie.rstrip(";"))
        else:
            parts.extend(f"{c.name}={c.value}" for c in (self.jar or []))
        if with_ticket and _BOOT.get("ticket"):
            parts.append(f"bili_ticket={_BOOT['ticket']}")
        return "; ".join(x for x in parts if x)

    def _nav_login_mid(self) -> int:
        """用 cookie 问 nav: 登录有效 → 返回 mid; 未登录/风控/网络异常 → 0。

        结果缓存 5 分钟(重连风暴时别把 nav 打成风控)。
        """
        with _NAV_LOCK:
            now = time.time()
            if _NAV.get("until", 0) > now:
                return int(_NAV.get("mid") or 0)
            mid = 0
            try:
                j = _http_json("https://api.bilibili.com/x/web-interface/nav",
                               self.plain_opener,
                               headers={"Cookie": self._cookie_header()})
                d = j.get("data") or {}
                if j.get("code") == 0 and d.get("isLogin"):
                    mid = int(d.get("mid") or 0)
                else:
                    self._log(f"cookie 未登录(code={j.get('code')}) → 按游客连弹幕(uid=0)",
                              gray=True)
            except Exception as e:  # noqa: BLE001
                mid = 0
                self._log(f"nav 探测失败({type(e).__name__}) → 按游客 uid=0", gray=True)
            _NAV.update({"until": now + 300, "mid": mid})
            return mid

    def _auth_uid(self) -> int:
        """auth.uid —— 必须与 getDanmuInfo 下发的 token 同源(见 _NAV 注释)。

        旧实现直接取 cookie 里的 DedeUserID: SESSDATA 失效那一刻起就永久死循环,
        因为 token 已经是游客的, uid 还是登录的。现在一律问 nav。
        """
        return self._nav_login_mid()

    def _boot(self) -> dict:
        """风控启动包: buvid cookie + bili_ticket + WBI mixin_key(缓存 2 天)。"""
        with _BOOT_LOCK:
            now = time.time()
            if _BOOT.get("until", 0) > now and _BOOT.get("mixin"):
                return _BOOT
            # 1) GenWebTicket (HMAC-SHA256, 密钥/流程来自公开文档存档)
            ts = int(now)
            hexsign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
            url = ("https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
                   f"?key_id=ec02&hexsign={hexsign}&context%5Bts%5D={ts}&csrf=")
            req = urllib.request.Request(url, method="POST", data=b"", headers={
                "User-Agent": UA, "Referer": "https://www.bilibili.com/"})
            with self.opener.open(req, timeout=10) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            if j.get("code") != 0:
                raise RuntimeError(f"GenWebTicket 失败: code={j.get('code')} {j.get('message') or ''}")
            d = j.get("data") or {}
            ticket = d.get("ticket") or ""
            img = ((d.get("nav") or {}).get("img")) or ""
            sub = ((d.get("nav") or {}).get("sub")) or ""
            m1 = re.search(r"([0-9a-f]{32})\.\w+", img)
            m2 = re.search(r"([0-9a-f]{32})\.\w+", sub)
            img_key, sub_key = (m1.group(1) if m1 else ""), (m2.group(1) if m2 else "")
            if not (img_key and sub_key):  # 回退: nav 接口(未登录也返回 wbi_img)
                j2 = _http_json("https://api.bilibili.com/x/web-interface/nav", self.opener)
                w = ((j2.get("data") or {}).get("wbi_img")) or {}
                m1 = re.search(r"([0-9a-f]{32})\.\w+", w.get("img_url") or "")
                m2 = re.search(r"([0-9a-f]{32})\.\w+", w.get("sub_url") or "")
                img_key, sub_key = (m1.group(1) if m1 else ""), (m2.group(1) if m2 else "")
            if not (img_key and sub_key):
                raise RuntimeError("未取得 WBI img_key/sub_key")
            raw = img_key + sub_key
            mixin = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw))[:32]
            _BOOT.update({"until": now + 86400 * 2, "ticket": ticket, "mixin": mixin})
            return _BOOT

    def _resolve_room(self):
        j = _http_json(
            f"https://api.live.bilibili.com/room/v1/Room/room_init?id={self.room_input}",
            self.opener)
        if j.get("code") != 0:
            raise RuntimeError(f"room_init 失败: {j.get('message') or j.get('code')}")
        d = j["data"]
        self.room_id = int(d.get("room_id") or 0)
        self.live_status = int(d.get("live_status", -1))
        if not self.room_id:
            raise RuntimeError("未拿到真实房间号")

    def _get_danmu_info(self):
        boot = self._boot()
        signed = _wbi_sign({"id": str(self.room_id), "type": "0"}, boot["mixin"])
        qs = urllib.parse.urlencode(signed)
        url = (f"https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?{qs}")
        jar_cookies = "; ".join(f"{c.name}={c.value}" for c in (self.jar or []))
        cookie = self._cookie_header() or jar_cookies
        j = _http_json(url, self.plain_opener, headers={"Cookie": cookie})
        if j.get("code") != 0:
            raise RuntimeError(f"getDanmuInfo 失败: code={j.get('code')} {j.get('message') or ''}")
        d = j["data"]
        hosts = d.get("host_list") or [{"host": "broadcastlv.chat.bilibili.com", "wss_port": 443}]
        return d.get("token", ""), hosts

    # ────────────────────────── 线程主体 ──────────────────────────

    def run(self):
        self._make_opener()
        while not self.stop.is_set():
            try:
                self._boot()
                self._resolve_room()
            except Exception as e:
                self._log(f"房间解析失败: {e}", err=True)
                if self.stop.wait(self.retry_sec):
                    return
                continue
            if self.live_status == 1:
                self._log(f"真实房间 {self.room_id} · 正在直播", green=True)
            elif self.live_status == 0:
                # 未开播: 不反复握手(服务端会立刻关闭连接), 低频探活即可
                if not self._offline_notified:
                    self._offline_notified = True
                    self._log(f"真实房间 {self.room_id} · 未开播 (低频探活中, 开播自动接入)", yellow=True)
                if self.stop.wait(60):
                    return
                continue
            else:
                self._log(f"真实房间 {self.room_id} · live_status={self.live_status}", yellow=True)
            self._offline_notified = False
            try:
                self._session()
            except WSClosed as e:
                self._log(f"WS 断开: {e}", yellow=True)
            except Exception as e:
                self._log(f"会话异常: {e}", err=True)
            if self.stop.is_set():
                return
            self._log(f"{self.retry_sec}s 后重连…", gray=True)
            if self.stop.wait(self.retry_sec):
                return

    def _session(self):
        token, hosts = self._get_danmu_info()
        host = hosts[0].get("host") or "broadcastlv.chat.bilibili.com"
        port = int(hosts[0].get("wss_port") or 443)
        ws = WSClient(f"wss://{host}:{port}/sub",
                      extra_headers={"Origin": "https://live.bilibili.com",
                                     "User-Agent": UA},
                      name=f"bili:{self.room_id}").connect()
        try:
            auth = json.dumps({
                "uid": self._auth_uid(), "roomid": self.room_id, "protover": 2,
                "platform": "web", "type": 2, "key": token,
                "clientver": "2.0.11",
            }).encode("utf-8")
            ws.send_binary(_pack(OP_AUTH, auth))
            self._log(f"已连接 wss://{host}:{port}/sub, 等待鉴权…", gray=True)

            hb = threading.Thread(target=self._heartbeat_loop, args=(ws,), daemon=True)
            hb.start()
            self._drain(ws)
        finally:
            ws.close()

    def _heartbeat_loop(self, ws: WSClient):
        while not self.stop.is_set():
            try:
                ws.send_binary(_pack(OP_HEARTBEAT, b"[object Object]"))
            except Exception:
                return
            if self.stop.wait(30):
                return

    def _drain(self, ws: WSClient):
        backoff = 3.0
        while not self.stop.is_set():
            try:
                _op, body = ws.recv_message(timeout=10)
            except socket.timeout:
                self._flush_expired()
                continue
            now = time.time()
            if len(body) < HEADER_LEN:
                continue
            total, _hl, protover, op, _seq = struct.unpack(">IHHII", body[:HEADER_LEN])
            payload = body[HEADER_LEN:total]
            if op == OP_AUTH_REPLY:
                code = None
                try:
                    code = json.loads(payload.decode("utf-8", "replace")).get("code")
                except Exception:
                    pass
                if code == 0:
                    backoff = 3.0
                    self._log("B站弹幕连接鉴权成功 ✓", green=True)
                else:
                    raise WSClosed(f"鉴权被拒 code={code}")
            elif op == OP_HEARTBEAT_REPLY:
                if len(payload) >= 4:
                    pop = struct.unpack(">I", payload[:4])[0]
                    self._log_dim(f"人气值 {pop}")
            elif op == OP_MESSAGE:
                for cmd, data in _decode_messages(payload, protover):
                    self._dispatch(cmd, data)
                self._flush_expired(now)
            # 其他 op 忽略

    # ────────────────────────── 命令分发 ──────────────────────────

    def _dispatch(self, cmd: str, data):
        p = self.emit
        if cmd.startswith("DANMU_MSG"):
            try:
                info = data.get("info") or []
                text = str(info[1] or "")
                info2 = info[2] if len(info) > 2 and isinstance(info[2], list) else []
                nick = str(info2[1] or "") if len(info2) > 1 else ""
            except Exception:
                return
            if text or nick:
                p({"platform": "bilibili", "type": "Chat", "data": {"name": nick, "content": text}})
        elif cmd == "INTERACT_WORD":
            d = data.get("data") or {}
            nick = d.get("uname") or ""
            mt = int(d.get("msg_type") or 0)
            if mt == 1 and nick:
                p({"platform": "bilibili", "type": "EnterRoom", "data": {"name": nick}})
            elif mt == 2 and nick:
                p({"platform": "bilibili", "type": "Follow", "data": {"name": nick}})
        elif cmd == "LIKE_INFO_V3_CLICK":
            d = data.get("data") or {}
            nick = d.get("uname") or ""
            if nick:
                p({"platform": "bilibili", "type": "Like",
                   "data": {"name": nick, "count": int(d.get("like_count") or 1)}})
        elif cmd in ("SEND_GIFT", "COMBO_SEND"):
            if self.debug:
                try:
                    dim_log(f"[GIFT样例] {cmd} {json.dumps(data, ensure_ascii=False)[:700]}")
                except Exception:
                    pass
            d = data.get("data") or {}
            try:
                self._gift_tick(d)
            except Exception:
                if self.debug:
                    self._log(f"礼物解析失败: {cmd}", err=True)
        elif cmd in ("SEND_GIFT_V2", "COMBO_SEND_V2") or ("GIFT" in cmd and "V2" in cmd):
            if self.debug:
                try:
                    dim_log(f"[GIFT样例] {cmd} {json.dumps(data, ensure_ascii=False)[:500]}")
                except Exception:
                    pass
            d = data.get("data") or {}
            nick = _parse_gift_v2(str(d.get("pb") or ""))
            if nick:
                p({"platform": "bilibili", "type": "Gift",
                   "data": {"name": nick, "item": "礼物"}})
            elif self.debug:
                dim_log("[GIFT_V2] 昵称提取失败")
        elif cmd == "GUARD_BUY":
            d = data.get("data") or {}
            nick = d.get("uname") or ""
            item = GUARD_NAMES.get(int(d.get("guard_level") or 0), d.get("gift_name") or "大航海")
            num = max(1, int(d.get("num") or 1))
            if nick:
                p({"platform": "bilibili", "type": "Subscribe",
                   "data": {"name": nick, "item": item, "num": num}})
        elif cmd == "SUPER_CHAT_MESSAGE":
            d = data.get("data") or {}
            u = d.get("user_info") or {}
            nick = u.get("uname") or ""
            if nick:
                p({"platform": "bilibili", "type": "SuperChat",
                   "data": {"name": nick, "content": d.get("message") or "",
                            "price": float(d.get("price") or 0)}})
        elif cmd == "PREPARING":
            p({"platform": "bilibili", "type": "EndLive", "data": {}})
        elif cmd == "LIVE":
            self._log("检测到开播信号 LIVE", green=True)
        elif cmd == "INTERACT_WORD_V2":
            d = data.get("data") or {}
            nick, mt, ev_room = _parse_interact_v2(str(d.get("pb") or ""))
            if self.debug:
                try:
                    dim_log(f"[V2解析] nick={nick!r} msg_type={mt} room={ev_room}")
                    if mt == 0:
                        pb_s = str(d.get("pb") or "")
                        dim_log(f"[V2零类型样本] pb={pb_s[:360]}")
                except Exception:
                    pass
            if ev_room and ev_room != self.room_id:
                return  # 事件不属于当前房间, 丢弃
            if mt == 1 and nick:
                p({"platform": "bilibili", "type": "EnterRoom", "data": {"name": nick}})
            elif mt == 2 and nick:
                p({"platform": "bilibili", "type": "Follow", "data": {"name": nick}})
        elif cmd in ("USER_TOAST_MSG", "USER_TOAST_MSG_V2"):
            d = data.get("data") or {}
            if self.debug:
                try:
                    dim_log(f"[TOAST样例] {json.dumps(d, ensure_ascii=False)[:400]}")
                except Exception:
                    pass
            nick = d.get("username") or d.get("uname") or ""
            if nick and d.get("unit"):
                # 舰长开通 toast(GUARD_BUY 一般同时有, reader 靠同人同物去重)
                item = str(d.get("unit") or "")
                p({"platform": "bilibili", "type": "Subscribe",
                   "data": {"name": nick, "item": item,
                            "num": max(1, int(d.get("num") or 1))}})
        elif cmd in ("ONLINE_RANK_COUNT",
                     "ONLINE_RANK_V2", "ONLINE_RANK_TOP3", "HOT_RANK_CHANGED",
                     "HOT_RANK_CHANGED_V2", "ROOM_REAL_TIME_MESSAGE_UPDATE",
                     "STOP_LIVE_ROOM_LIST", "ROOM_CHANGE", "NOTICE_MSG",
                     "WIDGET_BANNER", "ENTRY_EFFECT", "COMBO_END", "WATCHED_CHANGE",
                     "POPULARITY_RED_POCKET_NEW", "POPULARITY_RED_POCKET_START",
                     "POPULARITY_RED_POCKET_WINNER_LIST", "LIKE_INFO_V3_UPDATE",
                     "LIKE_INFO_V3_NOTICE", "HOT_BUY_NUM", "RANK_CHANGED_V2",
                     "ONLINE_RANK_V3"):
            if self.debug:
                self._log_dim(f"[忽略] {cmd}")
        else:
            if self.debug:
                self._unknown[cmd] = self._unknown.get(cmd, 0) + 1
                if self._unknown[cmd] <= 2:
                    self._log_dim(f"[未知] {cmd}")

    # ────────────────────────── 礼物连击聚合 ──────────────────────────

    def _gift_emit(self, key: str, nick: str, gname: str, num: int,
                   price_zi: float, combo: bool):
        uid_key = f"{nick}|{gname}" if not key else key
        now = time.time()
        with self._agg_lock:
            ent = self._agg.get(uid_key)
            if ent is None:
                ent = {"name": nick, "item": gname, "num": num, "price_zi": price_zi,
                       "last": now, "combo": combo}
                self._agg[uid_key] = ent
            else:
                ent["num"] = max(ent["num"], num)
                ent["name"] = nick or ent["name"]
                ent["item"] = gname or ent["item"]
                ent["price_zi"] = price_zi or ent["price_zi"]
                ent["combo"] = ent["combo"] or combo
                ent["last"] = now
        if not combo:
            self._flush_key(uid_key, force=True)

    def _gift_tick(self, d: dict):
        uid = d.get("uid") or d.get("sender_uin") or (d.get("send_master") or {})
        uname = d.get("uname") or ""
        gname = d.get("giftName") or ""
        gid = d.get("giftId") or d.get("gift_id") or gname
        num = max(int(d.get("num") or 1), 1)
        combo_num = int(float(d.get("combo_num") or d.get("batch_combo_num") or 0) or 0)
        total = max(num, combo_num)
        combo = bool(d.get("combo_send")) or bool(d.get("batch_combo_id")) or combo_num > 1
        price_zi = float(d.get("price") or 0)  # 金瓜子/个
        key = f"{uid}|{gid}"
        now = time.time()
        with self._agg_lock:
            ent = self._agg.get(key)
            if ent is None:
                ent = {"name": uname, "item": gname, "num": total, "price_zi": price_zi,
                       "last": now, "combo": combo}
                self._agg[key] = ent
            else:
                ent["num"] = max(ent["num"], total)
                ent["name"] = uname or ent["name"]
                ent["item"] = gname or ent["item"]
                ent["price_zi"] = price_zi or ent["price_zi"]
                ent["combo"] = ent["combo"] or combo
                ent["last"] = now
        if not combo:
            self._flush_key(key, force=True)

    def _flush_expired(self, now: float | None = None):
        now = now or time.time()
        for key in list(self._agg.keys()):
            with self._agg_lock:
                ent = self._agg.get(key)
                if ent and ent.get("combo") and now - ent["last"] >= self.combo_wait:
                    self._flush_key(key, force=True)

    def _flush_key(self, key: str, force: bool = False):
        with self._agg_lock:
            ent = self._agg.pop(key, None)
        if not ent:
            return
        n = ent["num"]
        yuan = ent["price_zi"] * n / 1000.0
        self.emit({"platform": "bilibili", "type": "Gift",
                   "data": {"name": ent["name"], "item": ent["item"], "num": n,
                            "price_yuan": round(yuan, 1)}})

    # ────────────────────────── 杂项 ──────────────────────────

    def _log(self, msg: str, green=False, yellow=False, gray=False, err=False):
        from logutil import platform_log
        platform_log("B站", msg, green=green, yellow=yellow, gray=gray, err=err)

    def _log_dim(self, msg: str):
        from logutil import dim_log
        dim_log(msg)


def _decode_messages(payload: bytes, protover: int):
    """把 op=5 的 payload 解成 [(cmd, data), ...]。"""
    out = []
    if protover == 2:
        try:
            payload = zlib.decompress(payload)
        except zlib.error:
            return out
    elif protover != 0:
        return out  # brotli(3) 等不处理
    # protover 0/2 解压后是一串连续包
    off = 0
    n = len(payload)
    while off + HEADER_LEN <= n:
        total, hl, _pv, _op, _sq = struct.unpack(">IHHII", payload[off:off + HEADER_LEN])
        if total < HEADER_LEN or off + total > n:
            break
        body = payload[off + hl:off + total]
        off += total
        try:
            j = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            continue
        if isinstance(j, list):
            for item in j:
                if isinstance(item, dict):
                    out.append((str(item.get("cmd") or ""), item))
        elif isinstance(j, dict):
            out.append((str(j.get("cmd") or ""), j))
    return out
