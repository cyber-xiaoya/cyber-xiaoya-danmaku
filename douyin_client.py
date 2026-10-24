#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""douyin_client.py — 抖音采集适配器(对接本地 douyinLive 服务)。

与旧版(douyin-danmaku-tts)的差异: 本文件完全独立, 只读解析同一 JSON 事件流,
并归一化为 {platform, type, data} 事件。服务端口独立(默认 1089),
不影响旧项目(1088)。

消息形态(douyinLive 服务推送):
  {"type":"system", "code":"ROOM_ONLINE|ROOM_OFFLINE|...", "message":...}
  {"method":"WebcastChatMessage",  "user":{"nickname":...}, "content":...}
  {"method":"WebcastGiftMessage",  "gift":{"name":...}, "repeatCount"/"comboCount"/...
                                   "groupId", "repeatEnd"}
  {"method":"WebcastBindingGiftMessage", "msg":{...礼物同上}}
  {"method":"WebcastLightGiftMessage",   ...}
  {"method":"WebcastMemberMessage",  "action":1}   进场
  {"method":"WebcastSocialMessage",  "action":1}   关注
  {"method":"WebcastLikeMessage",    "count":N}    点赞
"""

import json
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from wsclient import WSClient, WSClosed

ROOT = Path(__file__).resolve().parent
CREATE_NO_WINDOW = 0x08000000


def _as_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _port_up(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_service(http_base: str, exe_rel: str = "bin/douyinLive.exe", log_name="douyinLive.log") -> subprocess.Popen | None:
    """若本地服务未运行则拉起 douyinLive.exe(带自身 config.yaml)。返回进程句柄。"""
    host, _, port_s = http_base.removeprefix("http://").removeprefix("https://").partition(":")
    port = int(port_s or 80)
    if _port_up(host, port):
        return None
    exe = ROOT / exe_rel
    if not exe.exists():
        raise SystemExit(f"[error] 找不到 {exe}, 请检查安装")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logf = open(log_dir / log_name, "ab")
    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    for _ in range(40):  # 最多 20s
        if _port_up(host, port):
            return proc
        time.sleep(0.5)
    raise SystemExit(f"[error] douyinLive 服务 20s 内未就绪, 请查看 logs/{log_name}")


class DouyinClient(threading.Thread):
    def __init__(self, room_id: str, emit, cfg: dict | None = None):
        super().__init__(name=f"dy-{room_id}", daemon=True)
        self.room_id = str(room_id).strip()
        self.emit = emit
        self.cfg = cfg or {}
        self.ws_base = self.cfg.get("ws_base", "ws://127.0.0.1:1089").rstrip("/")
        self.http_base = self.ws_base.replace("ws://", "http://").replace("wss://", "https://")
        self.stop = threading.Event()
        self.debug = bool(self.cfg.get("debug"))
        self.online: bool | None = None
        self._proc = None

    def start_service(self):
        try:
            self._proc = ensure_service(self.http_base)
            if self._proc:
                from logutil import platform_log
                platform_log("抖音", "已自动拉起 douyinLive 服务 (独立实例)", green=True)
        except SystemExit:
            raise
        self._probe_status()

    def _probe_status(self):
        try:
            with urllib.request.urlopen(
                    f"{self.http_base}/api/v1/rooms/{self.room_id}/status", timeout=5) as r:
                j = json.loads(r.read().decode("utf-8"))
            d = (j.get("data") or {}) if isinstance(j, dict) else {}
            live = bool(d.get("is_live"))
            name = d.get("live_name") or ""
            from logutil import platform_log
            if live:
                platform_log("抖音", f"服务在线 · 直播间「{name}」正在直播", green=True)
            else:
                platform_log("抖音", f"服务在线 · 房间当前未开播 (保持等待)", yellow=True)
        except Exception:
            from logutil import platform_log
            platform_log("抖音", "状态查询失败(服务可能仍在启动)", yellow=True)

    def run(self):
        backoff = 3
        while not self.stop.is_set():
            try:
                self._session()
            except (WSClosed, WSClosedError) as e:
                from logutil import platform_log
                platform_log("抖音", f"连接断开: {e}", yellow=True)
            except Exception as e:
                from logutil import platform_log
                platform_log("抖音", f"会话异常: {e}", err=True)
            if self.stop.is_set():
                return
            from logutil import platform_log
            platform_log("抖音", f"{backoff}s 后重连…", gray=True)
            if self.stop.wait(backoff):
                return
            backoff = min(backoff + 3, 30)

    def _session(self):
        url = f"{self.ws_base}/ws/{self.room_id}"
        ws = WSClient(url, name=f"dy:{self.room_id}").connect()
        try:
            from logutil import platform_log
            platform_log("抖音", f"弹幕流已连接 {url}", green=True)
            while not self.stop.is_set():
                try:
                    _op, payload = ws.recv_message(timeout=15)
                except socket.timeout:
                    continue  # 未开播时服务端无推送, 静默等下一轮
                if _op != 1:  # 文本帧
                    continue
                try:
                    data = json.loads(payload.decode("utf-8", "replace"))
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                self._handle(data)
        finally:
            ws.close()

    # ────────────────────────── 事件归一化 ──────────────────────────

    def _handle(self, data: dict):
        if data.get("type") == "system":
            self._system(data)
            return
        method = data.get("method", "")
        p = self.emit
        user = data.get("user") or {}
        nick = user.get("nickname") or ""
        if method == "WebcastChatMessage":
            p({"platform": "抖音", "type": "Chat",
               "data": {"name": nick, "content": data.get("content") or ""}})
        elif method == "WebcastGiftMessage":
            self._gift(data, nick)
        elif method == "WebcastBindingGiftMessage":
            inner = data.get("msg")
            if isinstance(inner, dict) and inner:
                self._gift(inner, (inner.get("user") or {}).get("nickname") or nick)
        elif method == "WebcastLightGiftMessage":
            if data.get("gift") or data.get("user"):
                self._gift(data, nick)
        elif method == "WebcastMemberMessage":
            if _as_int(data.get("action")) == 1 and nick:
                p({"platform": "抖音", "type": "EnterRoom", "data": {"name": nick}})
        elif method == "WebcastSocialMessage":
            if _as_int(data.get("action")) == 1 and nick:
                p({"platform": "抖音", "type": "Follow", "data": {"name": nick}})
        elif method == "WebcastLikeMessage":
            if nick:
                p({"platform": "抖音", "type": "Like",
                   "data": {"name": nick, "count": max(_as_int(data.get("count"), 1), 1)}})
        elif method in ("WebcastRoomUserSeqMessage", "WebcastRoomStatsMessage",
                        "WebcastControlMessage", "WebcastRoomMessage",
                        "WebcastFansclubMessage", "WebcastInRoomBannerMessage"):
            if self.debug:
                from logutil import dim_log
                dim_log(f"[抖音][忽略] {method}")
        else:
            if self.debug:
                from logutil import dim_log
                import json as _json
                dim_log(f"[抖音][未知] {method} {_json.dumps(data, ensure_ascii=False)[:160]}")

    def _gift(self, data: dict, nick: str):
        gift = data.get("gift") or {}
        count = max(
            _as_int(data.get("repeatCount")), _as_int(data.get("comboCount")),
            _as_int(data.get("totalCount")), _as_int(data.get("count")),
            _as_int(data.get("groupCount")), 1,
        )
        self.emit({"platform": "抖音", "type": "Gift",
                   "data": {"name": nick,
                            "item": gift.get("name") or "",
                            "num": count,
                            "diamond": _as_int(gift.get("diamondCount")),
                            "group_id": str(data.get("groupId") or ""),
                            "repeat_end": _as_int(data.get("repeatEnd"))}})

    def _system(self, data: dict):
        from logutil import platform_log
        code = data.get("code", "")
        msg = data.get("message") or ""
        if code == "ROOM_ONLINE":
            self.online = True
            platform_log("抖音", f"{msg} (主播: {data.get('live_name') or '未知'})", green=True)
        elif code in ("ROOM_OFFLINE", "ACCOUNT_OFFLINE_NO_ROOM", "ROOM_ENDED"):
            self.online = False
            platform_log("抖音", f"{msg} · 自动轮询等待开播", yellow=True)
        elif code == "ROOM_NOT_FOUND":
            platform_log("抖音", f"{msg or '直播间不存在或房间号无效'}", err=True)
        else:
            platform_log("抖音", f"{msg or code}", gray=True)


class WSClosedError(Exception):
    pass
