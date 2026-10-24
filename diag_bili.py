#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""diag_bili.py — 诊断 getDanmuInfo -352 风控"""
import http.cookiejar
import json
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
ROOM = sys_room = "21452505"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def get(url, headers=None, timeout=10):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with opener.open(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

# 1) 主站领 cookie
try:
    st, _ = get("https://www.bilibili.com/", {"Accept-Language": "zh-CN,zh;q=0.9"})
    print("www.bilibili.com:", st, "jar:", sorted(c.name for c in jar))
except Exception as e:
    print("www 失败:", e)

# 2) finger/spi 领 buvid3/buvid4
b3 = b4 = ""
try:
    st, body = get("https://api.bilibili.com/x/frontend/finger/spi")
    j = json.loads(body)
    b3 = (j.get("data") or {}).get("b_3", "")
    b4 = (j.get("data") or {}).get("b_4", "")
    print("finger/spi:", st, "b_3 len:", len(b3), "b_4 len:", len(b4))
except Exception as e:
    print("spi 失败:", e)

# 3) 手动 Cookie 试 getDanmuInfo
def try_danmu(cookie: str, tag: str):
    url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id={ROOM}&type=0"
    try:
        st, body = get(url, {"Cookie": cookie, "Referer": "https://live.bilibili.com/"})
        j = json.loads(body)
        print(f"[{tag}] HTTP {st} code={j.get('code')} token_len={len(((j.get('data') or {}).get('token')) or '')}")
        return j.get("code")
    except Exception as e:
        print(f"[{tag}] 异常: {e}")
        return None

cookie_manual = f"buvid3={b3}; buvid4={b4}; b_nut={int(time.time())}"
try_danmu(cookie_manual, "manual-buvid")
try_danmu("", "no-cookie")
