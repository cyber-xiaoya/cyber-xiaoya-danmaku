#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""cdp-console-probe.py — 用「客户端自己的提取逻辑」读视频号中控台，看它到底能看到什么。

用途: 回答"视频号抓到了没有 / 礼物会不会被抓到"这类问题 —— 探针看到什么, 采集线程就看到什么。
用法: py -3 cdp-console-probe.py [端口, 默认 9223]
"""
import json
import sys
import time
import urllib.request

from wsclient import WSClient, WSClosed
from wechat_client import JS_COLLECT

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9223
BASE = f"http://127.0.0.1:{PORT}"

# 结构与帧信息(诊断用): 看内容在不在 iframe 里、有哪些可疑容器
JS_DIAG = r"""
(() => {
  const frames = [...document.querySelectorAll('iframe')].map(f => {
    let acc = false, cnt = -1, url = '';
    try { url = f.contentWindow.location.href; } catch (e) { url = '(cross-origin)'; }
    try {
      const cd = f.contentDocument;
      if (cd) { acc = true; const sc = cd.querySelector('.comment__list'); cnt = sc ? sc.querySelectorAll('.live-message-item').length : -1; }
    } catch (e) { }
    return { src: (f.src || '').slice(0, 90), url: url.slice(0, 90), accessible: acc, comment_list: cnt };
  });
  const names = new Set();
  for (const el of document.querySelectorAll('[class]')) {
    for (const c of el.classList) if (/comment|gift|message|interact|anchor/i.test(c)) names.add(c);
  }
  return JSON.stringify({
    href: location.href, title: document.title,
    login_form: !!document.querySelector('input[type=password], .login, [class*=login]'),
    frames: frames, classes: [...names].slice(0, 40)
  });
})()
"""


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "probe"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    pages = [t for t in get("/json/list") if t.get("type") == "page"]
    if not pages:
        print("没有页面目标")
        return 1
    for p in pages:
        print(f"页面: {p.get('title','')[:40]} → {p.get('url','')[:100]}")
    target = next((p for p in pages if "channels.weixin.qq.com" in (p.get("url") or "")), pages[0])

    ws = WSClient(target["webSocketDebuggerUrl"], name="console-probe")
    ws.connect(timeout=10)
    seq = 0

    def ev(js, timeout=15):
        nonlocal seq
        seq += 1
        mid = seq
        ws.send_text(json.dumps({"id": mid, "method": "Runtime.evaluate",
                                 "params": {"expression": js, "returnByValue": True, "awaitPromise": False}}))
        deadline = time.time() + timeout
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
                return ((msg.get("result") or {}).get("result") or {}).get("value")
        return None

    print("\n── 页面结构 ──")
    diag = ev(JS_DIAG)
    if diag:
        d = json.loads(diag)
        print(f"URL    : {d['href']}")
        print(f"标题   : {d['title']}")
        print(f"像登录页: {d['login_form']}")
        if d["frames"]:
            for f in d["frames"]:
                print(f"  iframe: src={f['src']}  accessible={f['accessible']}  评论数={f['comment_list']}")
        else:
            print("  (页面上没有 iframe)")
        print(f"可疑类名: {', '.join(d['classes']) if d['classes'] else '(无)'}")

    print("\n── 客户端提取结果(JS_COLLECT 原样) ──")
    raw = ev(JS_COLLECT)
    if not raw:
        print("(无响应 / 超时)")
    else:
        try:
            data = json.loads(raw)
        except Exception:
            print(raw[:2000])
            data = None
        if data is not None:
            print(f"ok={data.get('ok')}  reason={data.get('reason','-')}  条数={data.get('count','-')}")
            for it in (data.get("items") or [])[-12:]:
                print(f"  [{it.get('badge','')}] {it.get('nick','')} → {it.get('content','')}")
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
