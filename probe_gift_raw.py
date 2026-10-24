#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_gift_raw.py — 抓抖音礼物原始报文, 看有没有价值(抖币)字段。

用法: py -3 probe_gift_raw.py [秒数] [房间号]
"""

import json
import sys
import time

from wsclient import WSClient, WSClosed

ROOM = sys.argv[2] if len(sys.argv) > 2 else "123456789"
SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0


def main():
    url = f"ws://127.0.0.1:1089/ws/{ROOM}"
    ws = WSClient(url, name="probe-gift")
    ws.connect(timeout=10)
    print(f"已连接 {url}, 采样 {SECS:.0f}s …", flush=True)
    end = time.time() + SECS
    seen = 0
    while time.time() < end:
        try:
            _op, payload = ws.recv_message(timeout=5.0)
        except WSClosed as e:
            print("连接关闭:", e)
            break
        except Exception:
            continue
        try:
            msg = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            continue
        method = msg.get("method") or ""
        if "Gift" not in method:
            continue
        data = msg
        gift = data.get("gift") or {}
        seen += 1
        print(f"\n[{seen}] method={method}")
        print(f"    顶层字段: {sorted(data.keys())}")
        print(f"    gift 字段: {json.dumps(gift, ensure_ascii=False)[:500]}")
        print(f"    价值线索: diamond={data.get('diamondCount')} total={data.get('totalCount')} "
              f"combo={data.get('comboCount')} repeat={data.get('repeatCount')}")
        if seen >= 6:
            break
    ws.close()
    if seen == 0:
        print("采样期内没有礼物事件 (可让观众送一个, 或延长采样时间)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
