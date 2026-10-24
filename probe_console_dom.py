#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""probe_console_dom.py — 摸视频号助手中控台真实 DOM 结构(主文档+iframe 逐层查)。"""
import json
import time
import urllib.request

from wsclient import WSClient, WSClosed

JS = r"""
(() => {
  const docs = [];
  const walk = (doc, tag, depth) => {
    if (!doc || depth > 4) return;
    let url = '';
    try { url = doc.location ? doc.location.href : ''; } catch (e) {}
    const q = (s) => { try { return doc.querySelectorAll(s).length; } catch (e) { return 'ERR'; } };
    docs.push({
      tag, url: String(url).slice(-80),
      comment__list: q('.comment__list'),
      live_message_item: q('.live-message-item'),
      message_content: q('.message-content'),
      username_desc: q('.message-username-desc'),
      any_comment_class: q('[class*="comment"]'),
      any_message_class: q('[class*="message"]'),
      any_danmu_class: q('[class*="danmu"],[class*="barrage"]'),
      any_live_class: q('[class*="live-"]')
    });
    [...doc.querySelectorAll('iframe')].forEach((f, i) => {
      try { walk(f.contentDocument, tag + '>iframe' + i, depth + 1); }
      catch (e) { docs.push({ tag: tag + '>iframe' + i, url: 'CROSS-ORIGIN ' + String(f.src||'').slice(0, 100) }); }
    });
  };
  walk(document, 'main', 0);
  const bodyText = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 600);
  return JSON.stringify({ docs, bodyText });
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
            if val:
                data = json.loads(val)
                for d in data["docs"]:
                    print(json.dumps(d, ensure_ascii=False))
                print("--- bodyText ---")
                print(data["bodyText"])
            else:
                print("eval 失败:", json.dumps(msg, ensure_ascii=False)[:400])
            break
    ws.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
