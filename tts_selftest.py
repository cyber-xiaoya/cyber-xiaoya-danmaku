#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""tts_selftest.py — 语音链路自检: 调桥接合成一句 WAV, 打印耗时与音色信息(不朗读)。"""

import json
import time
import urllib.request


def main():
    base = "http://127.0.0.1:8765"
    try:
        health = json.loads(urllib.request.urlopen(base + "/api/health", timeout=8).read())
        print(f"桥接健康: status={health.get('status')}")
    except Exception as e:
        print(f"桥接不可达: {e}")
        return 1
    try:
        persona = json.loads(urllib.request.urlopen(base + "/api/persona/list", timeout=8).read())
        print(f"当前音色: {persona.get('current', {}).get('voice')}")
    except Exception:
        pass

    t0 = time.time()
    req = urllib.request.Request(
        base + "/api/tts",
        data=json.dumps({"text": "多平台朗读器自检"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        data = urllib.request.urlopen(req, timeout=90).read()
    except Exception as e:
        print(f"合成失败: {e}")
        return 1
    ok = data[:4] == b"RIFF"
    print(f"合成{'成功' if ok else '返回异常'}: {len(data)} 字节, 耗时 {time.time() - t0:.1f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
