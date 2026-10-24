#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""logutil.py — 多平台朗读器共用控制台输出(ANSI 着色, 平台标签)。

另外提供"控制台输出同时落盘"(set_logfile)：控制台窗口被 ✕ 关掉或程序异常退出时,
logs\reader.log 里仍留有当时打印过的东西(含 TTS 引擎状态 / 热加载提示 / 异常栈),
排查"朗读器怎么自己没了"这类问题全靠它。
"""

import sys
import time

RESET = "\033[0m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
GRAY = "\033[90m"
BOLD = "\033[1m"

PLATFORM_COLORS = {"抖音": CYAN, "B站": MAGENTA, "视频号": YELLOW}

_LOG_FILE = None
_ANSI_RE = None


def set_logfile(path):
    """把后续所有输出同时追加写入该文件(带时间戳, 去掉 ANSI 颜色)。返回是否成功。"""
    global _LOG_FILE, _ANSI_RE
    try:
        import re as _re
        from pathlib import Path as _P
        p = _P(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FILE = open(p, "a", encoding="utf-8", buffering=1)
        _ANSI_RE = _re.compile(r"\033\[[0-9;]*m")
        _mirror("===== 朗读器启动 " + time.strftime("%Y-%m-%d %H:%M:%S") + " =====")
        return True
    except Exception:
        _LOG_FILE = None
        return False


def _mirror(msg: str):
    if _LOG_FILE is None:
        return
    try:
        clean = _ANSI_RE.sub("", msg) if _ANSI_RE else msg
        _LOG_FILE.write(time.strftime("%H:%M:%S") + " " + clean + "\n")
    except Exception:
        pass


def _enable_vt():
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


_enable_vt()
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def stamp() -> str:
    return f"{GRAY}{time.strftime('%H:%M:%S')}{RESET}"


def out(msg: str, end: str = "\n"):
    print(f"{stamp()} {msg}", end=end, flush=True)
    _mirror(msg)


def plain(msg: str, color: str = "", end: str = "\n"):
    print(f"{stamp()} {color}{msg}{RESET}" if color else f"{stamp()} {msg}", end=end, flush=True)
    _mirror(msg)


def dim_log(msg: str):
    print(f"{stamp()} {DIM}{msg}{RESET}", flush=True)
    _mirror(msg)


def platform_log(platform: str, msg: str, green=False, yellow=False, gray=False, err=False):
    color = PLATFORM_COLORS.get(platform, "")
    tag = f"{BOLD}{color}[{platform}]{RESET}" if color else f"[{platform}]"
    inner = GREEN if green else YELLOW if yellow else GRAY if gray else RED if err else ""
    body = f"{inner}{msg}{RESET}" if inner else msg
    print(f"{stamp()} {tag} {body}", flush=True)
    _mirror(f"[{platform}] {msg}")


def event_line(platform: str, icon: str, kind: str, body: str):
    """统一事件打印: [HH:MM:SS] [B站] 🎁 昵称 赠送 小心心 x1"""
    color = PLATFORM_COLORS.get(platform, "")
    tag = f"{BOLD}{color}[{platform}]{RESET}" if color else f"[{platform}]"
    print(f"{stamp()} {tag} {icon} {body}", flush=True)
    _mirror(f"[{platform}] {kind}: {body}")
