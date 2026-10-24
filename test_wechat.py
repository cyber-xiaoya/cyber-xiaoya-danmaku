#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""test_wechat.py — 视频号评论采集独立诊断(不朗读, 只打印)。

用法:
    py -3 test_wechat.py [秒数]
前置: 用「启动调试浏览器.cmd」打开的视频号助手页面停留在直播中控台
"""

import json
import sys
import time

from wechat_client import WechatConsoleClient


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    cfg = {"wechat": {"enabled": True, "cdp_port": 9223, "interval_ms": 1200, "debug": True},
           "debug": True}

    def on_event(ev):
        print("[事件] " + json.dumps(ev, ensure_ascii=False), flush=True)

    c = WechatConsoleClient(cfg, on_event)
    c.start()
    print(f"视频号采集诊断: 运行 {secs:.0f}s …", flush=True)
    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        pass
    c.stop.set()
    time.sleep(0.5)
    print("诊断结束", flush=True)


if __name__ == "__main__":
    main()
