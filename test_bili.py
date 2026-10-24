#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""test_bili.py — B站采集链路独立诊断(不含 TTS)。

用法:
  python test_bili.py <房间号或短号> [秒数, 默认 20]
"""

import sys
import threading
import time

from bilibili_client import BilibiliClient
from logutil import event_line, plain, GREEN, RED


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    room = sys.argv[1]
    dur = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    counts: dict = {}
    lock = threading.Lock()

    def emit(ev):
        t = ev.get("type")
        d = ev.get("data") or {}
        with lock:
            counts[t] = counts.get(t, 0) + 1
        icon = {"Chat": "💬", "Gift": "🎁", "Like": "👍", "EnterRoom": "👋",
                "Follow": "⭐", "Subscribe": "🛡️", "SuperChat": "💰", "EndLive": "📴"}.get(t, "·")
        if t == "Chat":
            event_line("B站", icon, t, f"{d.get('name')}: {d.get('content')}")
        elif t == "Gift":
            event_line("B站", icon, t, f"{d.get('name')} 赠送 {d.get('item')} x{d.get('num')} (≈{d.get('price_yuan')}元)")
        elif t == "Like":
            event_line("B站", icon, t, f"{d.get('name')} x{d.get('count')}")
        elif t == "Subscribe":
            event_line("B站", icon, t, f"{d.get('name')} 开通 {d.get('item')} x{d.get('num')}")
        elif t == "SuperChat":
            event_line("B站", icon, t, f"{d.get('name')} ¥{d.get('price')}: {d.get('content')}")
        elif t in ("EnterRoom", "Follow"):
            event_line("B站", icon, t, d.get("name") or "")
        else:
            event_line("B站", icon, t, "")

    plain(f"B站采集诊断: 房间 {room}, 运行 {dur}s …")
    c = BilibiliClient(room, emit, {"debug": True})
    c.start()
    time.sleep(dur)
    c.stop.set()
    time.sleep(0.5)
    total = sum(counts.values())
    if total:
        plain(f"收到事件 {total} 条: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), GREEN)
    else:
        plain(f"{dur}s 内未收到任何事件(房间可能未开播, 或该房间确实安静)", RED)


if __name__ == "__main__":
    main()
