#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""cdp_probe.py — 通过 Chrome DevTools 协议探测「视频号助手」网页的评论数据来源。

用法:
    py -3 cdp_probe.py [秒数] [--url-key 关键词]

做三件事:
  1. 列出调试浏览器的所有页面目标
  2. 挂到目标页, 打开 Network/Runtime 域
  3. 在指定时间内转储所有网络请求 URL 与 WebSocket 帧(截断显示)

目的: 找出评论是走 HTTP 轮询还是 WebSocket, 以及具体端点, 供正式采集端复用。
"""

import json
import sys
import time
import urllib.request

from wsclient import WSClient, WSClosed

CDP_BASE = "http://127.0.0.1:9223"


def _get(path: str, timeout: float = 8.0):
    req = urllib.request.Request(CDP_BASE + path, headers={"User-Agent": "cdp-probe"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_pages():
    out = []
    for t in _get("/json/list"):
        if t.get("type") == "page":
            out.append(t)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    duration = float(args[0]) if args else 30.0
    key = "channels.weixin.qq.com"
    for a in sys.argv[1:]:
        if a.startswith("--url-key"):
            key = a.split("=", 1)[1] if "=" in a else key

    pages = list_pages()
    if not pages:
        print("没有可挂载的页面目标; 请确认调试浏览器已打开")
        return 1

    print(f"发现 {len(pages)} 个页面目标:")
    for p in pages:
        print(f"  · {p.get('title','')[:60]}  →  {p.get('url','')[:110]}")

    target = None
    for p in pages:
        if key in (p.get("url") or ""):
            target = p
            break
    if target is None:
        target = pages[0]
        print(f"[提示] 未找到含 '{key}' 的页面, 改用第一个: {target.get('url','')[:80]}")
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        print("目标页缺少 webSocketDebuggerUrl")
        return 1

    print(f"\n挂载: {ws_url[:100]} …")
    ws = WSClient(ws_url, name="cdp")
    ws.connect(timeout=10)
    print("CDP 已连接 ✓\n")

    seq = 0

    def call(method, params=None):
        nonlocal seq
        seq += 1
        ws.send_text(json.dumps({"id": seq, "method": method, "params": params or {}}))
        return seq

    call("Network.enable")
    call("Runtime.enable")
    call("Page.enable")

    urls_seen = {}
    frames_seen = {}
    start = time.time()
    print(f"--- 抓取 {duration:.0f} 秒 (请在此期间让页面产生评论刷新) ---")
    while time.time() - start < duration:
        try:
            _op, payload = ws.recv_message(timeout=1.0)
        except WSClosed:
            print("[!] CDP 连接中断")
            break
        except Exception:
            continue
        try:
            msg = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            continue

        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "Network.requestWillBeSent":
            req = params.get("request") or {}
            url = req.get("url", "")
            if url.startswith("data:") or url.endswith(".js") or url.endswith(".css"):
                continue
            urls_seen[url] = urls_seen.get(url, 0) + 1
            if any(k in url for k in ("comment", "danmu", "interact", "msg", "live", "chat", "wss")):
                print(f"[REQ] {url[:150]}")
        elif method == "Network.webSocketCreated":
            print(f"[WS ] 新建连接: {params.get('url','')[:140]}")
        elif method == "Network.webSocketFrameReceived":
            r = params.get("response") or {}
            data = (r.get("payloadData") or "")[:220].replace("\n", " ")
            url = params.get("url", "")
            frames_seen[url] = frames_seen.get(url, 0) + 1
            print(f"[WS←] {url[:70]}  #{frames_seen[url]}  {data}")
        elif method == "Network.webSocketFrameSent":
            r = params.get("response") or {}
            data = (r.get("payloadData") or "")[:120].replace("\n", " ")
            print(f"[WS→] {data}")

    print("\n--- 汇总: 出现过的非静态请求 ---")
    for url, n in sorted(urls_seen.items(), key=lambda kv: -kv[1])[:40]:
        print(f"  x{n}  {url[:150]}")
    print("\n--- 汇总: WebSocket 连接及帧数 ---")
    for url, n in frames_seen.items():
        print(f"  {n} 帧  {url[:140]}")
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
