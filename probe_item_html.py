#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_item_html.py — 抓 .live-message-item 的真实 HTML 结构。"""
import json
import time
import urllib.request

from wsclient import WSClient, WSClosed

JS = r"""
(() => {
  const frames = [...document.querySelectorAll('iframe')];
  for (const f of frames) {
    try {
      const cd = f.contentDocument;
      if (!cd) continue;
      const items = [...cd.querySelectorAll('.live-message-item')];
      if (items.length) {
        return JSON.stringify({
          frame: String(f.src||'').slice(-60),
          count: items.length,
          items: items.slice(0, 3).map(el => el.outerHTML.slice(0, 1200))
        });
      }
    } catch (e) {}
  }
  return JSON.stringify({ found: false });
})()
"""


def main():
    req = urllib.request.Request("http://127.0.0.1:9223/json/list", headers={"User-Agent": "probe"})
    pages = [t for t in json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace"))
             if t.get("type") == "page"]
    target = next((p for p in pages if "channels.weixin.qq.com" in (p.get("url") or "")), None)
    if not target:
        print("没有视频号页面目标"); return 1
    ws = WSClient(target["webSocketDebuggerUrl"], name="probe")
    ws.connect(timeout=10)
    ws.send_text(json.dumps({"id": 1, "method": "Runtime.evaluate",
                             "params": {"expression": JS, "returnByValue": True}}))
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            _op, payload = ws.recv_message(timeout=1.0)
        except WSClosed:
            print("连接关闭"); return 1
        except Exception:
            continue
        try:
            msg = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            continue
        if msg.get("id") == 1:
            val = ((msg.get("result") or {}).get("result") or {}).get("value")
            print(val if val else json.dumps(msg, ensure_ascii=False)[:400])
            break
    ws.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
