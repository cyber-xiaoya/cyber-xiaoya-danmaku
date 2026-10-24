#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""bili-auth-probe.py — 定位「B站 WS 一连上就被关闭」的根因。

三组对照（每组 ~18 秒）:
  A. 完全照朗读器的方式（带 config 里的 cookie，uid=DedeUserID）
  B. 带 cookie，但把 auth 的 uid 强制成 0（游客 token 配游客 uid）
  C. 不带 cookie（uid=0，即 test_bili.py 的路径）
  D. 不带 cookie，但把 auth 的 uid 强制成 cookie 里的 DedeUserID
预期: A 被关闭 / B 鉴权成功 / C 鉴权成功 / D 被关闭  →  说明是「uid 与 token 不匹配」
"""
import json
import re
import sys
import threading
import time
from pathlib import Path

from bilibili_client import BilibiliClient

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
BILI = CFG["bilibili"]
ROOM = str(BILI["room_id"])
COOKIE = str(BILI.get("cookie") or "")
UID = int(re.search(r"DedeUserID=(\d+)", COOKIE).group(1)) if re.search(r"DedeUserID=(\d+)", COOKIE) else 0
print(f"room={ROOM}  cookie 长度={len(COOKIE)}  DedeUserID={UID}\n")

RESULTS = {}
_orig_auth_uid = BilibiliClient._auth_uid


def run(tag, cfg, force_uid=None, seconds=18):
    events = []
    lines = []

    def emit(ev):
        events.append(ev)

    if force_uid is None:
        BilibiliClient._auth_uid = _orig_auth_uid
    else:
        BilibiliClient._auth_uid = lambda self, _u=force_uid: _u

    c = BilibiliClient(ROOM, emit, cfg)
    orig_log = c._log

    def log(msg, **kw):
        lines.append(msg)
        orig_log(msg, **kw)

    c._log = log
    print(f"── 用例 {tag} ──")
    c.start()
    time.sleep(seconds)
    c.stop.set()
    time.sleep(0.5)
    BilibiliClient._auth_uid = _orig_auth_uid

    ok = any("鉴权成功" in x for x in lines)
    closed = sum(1 for x in lines if "连接被关闭" in x or "WS 断开" in x)
    rejected = [x for x in lines if "鉴权被拒" in x]
    for x in lines:
        print(f"   {x}")
    print(f"   → 鉴权成功={ok}  WS断开次数={closed}  事件={len(events)}  拒绝={rejected}\n")
    RESULTS[tag] = ok


run("A 带cookie+uid=DedeUserID（=朗读器现状）", BILI, force_uid=None)
run("B 带cookie+uid强制0", BILI, force_uid=0)
run("C 不带cookie（=test_bili.py）", {"debug": True}, force_uid=None)
run("D 不带cookie+uid强制DedeUserID", {"debug": True}, force_uid=UID)

print("=== 汇总 ===")
for k, v in RESULTS.items():
    print(f"  {'✅ 鉴权成功' if v else '❌ 失败'}  {k}")
