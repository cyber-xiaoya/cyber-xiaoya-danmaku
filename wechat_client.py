#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""wechat_client.py — 视频号直播评论采集(经 Chrome DevTools 协议读中控台 DOM)。

原理:
    视频号直播没有公开弹幕接口, 但「视频号助手」网页的直播中控台
    (channels.weixin.qq.com/platform/live/liveBuild) 会实时渲染评论/弹幕:
        .live-message-item
            .finder-ui-live-plain-badge__name-text   (身份标签: 粉丝/主播/通知)
            .message-username-desc                   (昵称:)
            .message-content                         (内容)
    本模块用一个带调试端口的独立浏览器实例(默认 9223), 通过 CDP 每秒读一次
    该列表, 增量提取新评论 → 交给朗读器。浏览器最小化/被遮挡都不影响。

依赖: 仅标准库 + 项目自带 wsclient.py
"""

import json
import re
import threading
import time
import urllib.request
from collections import deque

from logutil import dim_log, platform_log
from wsclient import WSClient, WSClosed

# 在页面里执行的取数脚本: 找到含评论列表的 iframe(或主文档), 抽出所有评论条目
JS_COLLECT = r"""
(() => {
  const pick = () => {
    const frames = [...document.querySelectorAll('iframe')];
    for (const f of frames) {
      try {
        const cd = f.contentDocument;
        if (cd && cd.querySelector && cd.querySelector('.comment__list')) return cd;
      } catch (e) { }
    }
    if (document.querySelector('.comment__list')) return document;
    return null;
  };
  const d = pick();
  if (!d) return JSON.stringify({ ok: false, reason: 'no-console' });
  const sc = d.querySelector('.comment__list');
  const items = [...sc.querySelectorAll('.live-message-item')];
  const out = items.map(el => {
    const n = el.querySelector('.message-username-desc');
    const c = el.querySelector('.message-content');
    const b = el.querySelector('.finder-ui-live-plain-badge__name-text')
           || el.querySelector('.message-type');
    return {
      nick: n ? n.innerText.replace(/\s+/g, ' ').trim() : '',
      content: c ? c.innerText.replace(/\s+/g, ' ').trim() : '',
      badge: b ? b.innerText.replace(/\s+/g, ' ').trim() : ''
    };
  });
  return JSON.stringify({ ok: true, count: items.length, items: out });
})()
"""

# 系统/非真人评论的身份标签, 直接忽略
SKIP_BADGES = {"通知", "主题", "系统", "公告"}
NICK_TAIL = re.compile(r"[:：]\s*$")
CONSOLE_URL = "https://channels.weixin.qq.com/platform/live/liveBuild"


class WechatConsoleClient(threading.Thread):
    """视频号直播评论采集线程(CDP 版)。"""

    def __init__(self, cfg: dict, on_event):
        super().__init__(name="wechat-console", daemon=True)
        w = cfg.get("wechat") or {}
        self.port = int(w.get("cdp_port", 9223))
        self.interval = max(0.3, w.get("interval_ms", 1200) / 1000.0)
        self.debug = bool(w.get("debug", cfg.get("debug", False)))
        self.cfg = cfg
        self.on_event = on_event
        self.stop = threading.Event()
        self.ws: WSClient | None = None
        self._seq = 0
        self._recent: deque = deque(maxlen=24)   # 已读指纹, 用于增量判断
        self._baseline_done = False
        self._last_warn = 0.0
        self._miss = 0
        self._last_nav = 0.0
        self.status = "未连接"

    # ────────────────────── CDP 连接 ──────────────────────

    def _http_json(self, path: str, timeout: float = 5.0):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers={"User-Agent": "wechat-console"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _find_page(self) -> dict | None:
        """找「视频号助手」页面目标(含中控台/弹幕页)。"""
        try:
            pages = [t for t in self._http_json("/json/list") if t.get("type") == "page"]
        except Exception:
            return None
        for p in pages:
            if "channels.weixin.qq.com" in (p.get("url") or ""):
                return p
        return pages[0] if pages else None

    def _connect(self) -> bool:
        page = self._find_page()
        if not page or not page.get("webSocketDebuggerUrl"):
            return False
        try:
            ws = WSClient(page["webSocketDebuggerUrl"], name="cdp-wechat")
            ws.connect(timeout=8)
        except Exception as e:
            if self.debug:
                dim_log(f"[视频号] CDP 连接失败: {e}")
            return False
        self.ws = ws
        self._seq = 0
        self.status = f"已连接 {page.get('url','')[:48]}"
        platform_log("视频号", f"中控台已挂载 · {page.get('url','')[:60]}")
        return True

    def _eval(self, js: str, timeout: float = 6.0) -> str | None:
        if self.ws is None:
            return None
        self._seq += 1
        mid = self._seq
        try:
            self.ws.send_text(json.dumps({
                "id": mid, "method": "Runtime.evaluate",
                "params": {"expression": js, "returnByValue": True, "awaitPromise": False},
            }))
        except Exception:
            self._drop_conn()
            return None
        deadline = time.time() + timeout
        while time.time() < deadline and not self.stop.is_set():
            try:
                _op, payload = self.ws.recv_message(timeout=1.0)
            except WSClosed:
                self._drop_conn()
                return None
            except Exception:
                continue
            try:
                msg = json.loads(payload.decode("utf-8", "replace"))
            except Exception:
                continue
            if msg.get("id") != mid:
                continue
            res = ((msg.get("result") or {}).get("result") or {})
            if msg.get("error"):
                if self.debug:
                    dim_log(f"[视频号] CDP 错误: {str(msg['error'])[:120]}")
                return None
            val = res.get("value")
            return val if isinstance(val, str) else (json.dumps(val, ensure_ascii=False) if val is not None else None)
        return None

    def _drop_conn(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.ws = None
        self.status = "连接断开"

    # ────────────────────── 主循环 ──────────────────────

    def run(self):
        while not self.stop.is_set():
            if self.ws is None and not self._connect():
                now = time.time()
                if now - self._last_warn > 30:
                    self._last_warn = now
                    platform_log("视频号", "等待中控台页面 (请用「启动调试浏览器.cmd」打开视频号助手并进入直播间)")
                self.stop.wait(5.0)
                continue

            raw = self._eval(JS_COLLECT, timeout=6.0)
            if raw is None and self.debug:
                dim_log("[视频号] 本次轮询无响应 (CDP 超时)")
            if raw:
                try:
                    data = json.loads(raw)
                except Exception:
                    data = None
                if data and data.get("ok"):
                    self._miss = 0
                    self._handle_items(data.get("items") or [])
                elif data and data.get("reason") == "no-console":
                    self._maybe_navigate()
            else:
                self._miss += 1
            self.stop.wait(self.interval)
        self._drop_conn()

    def _maybe_navigate(self):
        """页面上找不到评论列表时, 自动把调试浏览器导航到直播中控台。"""
        self._miss += 1
        now = time.time()
        if self._miss >= 3 and now - self._last_nav > 60:
            self._last_nav = now
            self._miss = 0
            if self._eval(f"location.href='{CONSOLE_URL}'; 'nav'", timeout=5.0):
                platform_log("视频号", "已自动跳转到直播中控台, 等待页面加载…")
                self.stop.wait(9.0)
        elif self._miss == 3 and self.debug:
            dim_log("[视频号] 页面上没有评论列表 (等待自动跳转冷却)")

    def _handle_items(self, items):
        fresh = []
        for it in items:
            nick = (it.get("nick") or "").strip()
            content = (it.get("content") or "").strip()
            badge = (it.get("badge") or "").strip()
            if badge in SKIP_BADGES:
                continue
            if not content:
                continue
            nick = NICK_TAIL.sub("", nick)
            fp = f"{nick}|{content}"
            if fp in self._recent:
                continue
            self._recent.append(fp)
            if nick:
                fresh.append((nick, content))

        if not self._baseline_done:
            # 首次连接只建立基线, 不朗读历史评论
            self._baseline_done = True
            if fresh and self.debug:
                dim_log(f"[视频号] 基线 {len(fresh)} 条历史评论已跳过")
            return

        if self.debug:
            dim_log(f"[视频号] 轮询: 列表 {len(items)} 条, 新增 {len(fresh)} 条")

        for nick, content in fresh:
            self.on_event({
                "platform": "视频号",
                "type": "Chat",
                "data": {"name": nick, "content": content},
            })
