#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""yt_probe.py — 探路脚本: 不装任何第三方库, 只用 InnerTube(网页接口) 拉 YouTube 直播弹幕.

用法:
  python yt_probe.py <videoId或直播URL> [轮询秒数]

原理(和 pytchat / chat-downloader 同一套, 官方 API 之外的免 key 路子):
  1) GET /watch?v=<id>  → 从 ytInitialData 里拿 liveChatRenderer 的 continuation
  2) POST /youtubei/v1/live_chat/get_live_chat?key=<INNERTUBE_API_KEY>
     body={context:{client:{clientName:WEB, clientVersion:...}}, continuation:<token>}
     → actions[] 里是 addChatItemAction / addLiveChatTickerItemAction 等
  3) 响应里 continuationContents...continuations[0].invalidationContinuationData.continuation
     是下一轮 token; timeoutMs 是服务端建议的轮询间隔

本脚本只打印, 不做任何转发 —— 先确认能拿到弹幕再往 reader.py 里接。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CLIENT_VERSION = "2.20241010.00.00"


def parse_video_id(s: str) -> str:
    s = (s or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    q = urllib.parse.urlparse(s)
    if q.netloc.endswith("youtu.be"):
        return q.path.lstrip("/").split("/")[0]
    v = urllib.parse.parse_qs(q.query).get("v")
    if v:
        return v[0]
    m = re.search(r"/(?:live|embed)/([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else s


def find_livechat_continuation(data: dict) -> str | None:
    """在 ytInitialData 里挖 liveChatRenderer 的 continuation(结构经常变, 所以广搜)。"""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if "liveChatRenderer" in node:
                lcr = node["liveChatRenderer"] or {}
                for c in lcr.get("continuations") or []:
                    for k in ("reloadContinuationData", "invalidationContinuationData"):
                        if isinstance(c.get(k), dict) and c[k].get("continuation"):
                            found.append(c[k]["continuation"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found[0] if found else None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    vid = parse_video_id(sys.argv[1])
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA,
                         "Accept-Language": "en-US,en;q=0.9",
                         "Cookie": "CONSENT=YES+1; SOCS=CAI"})
    print(f"[1] 拉 chat 页: https://www.youtube.com/live_chat?v={vid}")
    r = sess.get(f"https://www.youtube.com/live_chat?v={vid}&is_popout=1", timeout=25)
    print(f"    HTTP {r.status_code}, {len(r.text)} 字节")
    if r.status_code != 200:
        print(r.text[:300])
        return
    html = r.text
    # ⚠ watch 页里挖不到 liveChatRenderer(直播页的聊天是懒加载的), 必须走 /live_chat 独立页;
    #   而且变量是 window["ytInitialData"] = {...}(带方括号/引号), 不是裸的 ytInitialData =
    m = re.search(r'window\["ytInitialData"\]\s*=\s*(\{.+?\});</script>', html, re.S)
    if not m:
        m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", html, re.S)
    if not m:
        print("    ✗ 没找到 ytInitialData")
        return
    data = json.loads(m.group(1))
    key_m = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    api_key = key_m.group(1) if key_m else ""
    cv_m = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    client_version = cv_m.group(1) if cv_m else CLIENT_VERSION
    print(f"    INNERTUBE_API_KEY={'有' if api_key else '无'} clientVersion={client_version}")
    # 聊天被主播关掉时, contents 里只有一条 messageRenderer: "Chat is disabled for this live stream."
    dis = re.search(r"Chat is disabled", html)
    if dis:
        print("    ✗ 这场直播没开聊天(Chat is disabled for this live stream.)")

    cont = find_livechat_continuation(data)
    if not cont:
        # 未开播 / 没开聊天 / 需要登录 三种情况都走这里
        cm = re.search(r'"liveChatId":"([^"]+)"', html)
        print(f"    ✗ 没挖到 liveChatRenderer·continuation (liveChatId={cm.group(1) if cm else '无'})")
        print("      → 可能: 直播没开聊天 / 已结束 / 需要登录(年龄限制)")
        return
    print(f"    ✓ continuation={cont[:40]}…")

    url = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
    if api_key:
        url += "?key=" + api_key
    ctx = {"context": {"client": {"clientName": "WEB", "clientVersion": CLIENT_VERSION,
                                  "hl": "en", "gl": "US"}}}
    t0 = time.time()
    n = 0
    while time.time() - t0 < seconds:
        body = dict(ctx, continuation=cont)
        rr = sess.post(url, json=body, timeout=20,
                       headers={"Content-Type": "application/json",
                                "Origin": "https://www.youtube.com",
                                "Referer": f"https://www.youtube.com/watch?v={vid}"})
        if rr.status_code != 200:
            print(f"    ✗ 轮询 HTTP {rr.status_code}: {rr.text[:200]}")
            return
        j = rr.json()
        actions = (j.get("continuationContents") or {}).get("liveChatContinuation", {}).get("actions") or []
        for a in actions:
            item = (a.get("addChatItemAction") or {}).get("item") or {}
            for kind, p in item.items():
                if not isinstance(p, dict):
                    continue
                msg = "".join(run.get("text", "") for run in
                              ((p.get("message") or {}).get("runs") or []))
                author = (p.get("authorName") or {}).get("simpleText") or ""
                if msg or author:
                    n += 1
                    print(f"    [{kind}] {author}: {msg[:80]}")
        # 下一轮 token
        conts = ((j.get("continuationContents") or {}).get("liveChatContinuation", {})
                 .get("continuations") or [])
        nxt, wait_ms = None, 3000
        for c in conts:
            for k in ("invalidationContinuationData", "timedContinuationData", "reloadContinuationData"):
                if isinstance(c.get(k), dict) and c[k].get("continuation"):
                    nxt = c[k]["continuation"]
                    wait_ms = int(c[k].get("timeoutMs") or c[k].get("timeoutDurationMs") or 3000)
                    break
            if nxt:
                break
        if not nxt:
            print("    ✗ 响应里没有下一轮 continuation(直播可能刚结束)")
            return
        cont = nxt
        time.sleep(max(1.0, min(10.0, wait_ms / 1000.0)))
    print(f"\n[总计] {n} 条消息 / {seconds}s")


if __name__ == "__main__":
    main()
