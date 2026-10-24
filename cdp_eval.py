#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""cdp_eval.py — 在调试浏览器的目标页上执行 JS 并打印结果(用于查看页面状态/DOM)。

用法:
    py -3 cdp_eval.py "document.title"
    py -3 cdp_eval.py --text 3000            # 打印页面可见文本前 3000 字
    py -3 cdp_eval.py --url-key channels.weixin.qq.com "location.href"
"""

import json
import sys
import time
import urllib.request

from wsclient import WSClient, WSClosed

CDP_BASE = "http://127.0.0.1:9223"


def _get(path: str, timeout: float = 8.0):
    req = urllib.request.Request(CDP_BASE + path, headers={"User-Agent": "cdp-eval"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_pages():
    return [t for t in _get("/json/list") if t.get("type") == "page"]


def main():
    argv = sys.argv[1:]
    want_text = None
    key = "channels.weixin.qq.com"
    expr = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--text":
            want_text = int(argv[i + 1]) if i + 1 < len(argv) and argv[i + 1].isdigit() else 3000
            i += 2
            continue
        if a.startswith("--url-key"):
            key = a.split("=", 1)[1] if "=" in a else key
            i += 1
            continue
        expr = a
        i += 1

    pages = list_pages()
    if not pages:
        print("没有页面目标")
        return 1
    print("页面目标:")
    for p in pages:
        print(f"  · {p.get('title','')[:50]}  →  {p.get('url','')[:110]}")

    target = next((p for p in pages if key in (p.get("url") or "")), pages[0])
    ws = WSClient(target["webSocketDebuggerUrl"], name="cdp-eval")
    ws.connect(timeout=10)

    seq = 0

    def call(method, params=None, wait=True):
        nonlocal seq
        seq += 1
        mid = seq
        ws.send_text(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not wait:
            return None
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                _op, payload = ws.recv_message(timeout=1.0)
            except WSClosed:
                return None
            except Exception:
                continue
            try:
                msg = json.loads(payload.decode("utf-8", "replace"))
            except Exception:
                continue
            if msg.get("id") == mid:
                return msg
        return None

    if want_text is not None:
        js = ("(() => { const t = document.body ? document.body.innerText : '';"
              f" return t.slice(0, {want_text}); }})()")
    else:
        js = expr or "document.title"

    resp = call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": False})
    ws.close()
    if not resp:
        print("执行超时/失败")
        return 1
    res = (resp.get("result") or {}).get("result") or {}
    val = res.get("value")
    print(f"\nURL: {target.get('url','')}")
    print("--- JS 结果 ---")
    print(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
