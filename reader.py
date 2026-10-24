#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""multi-danmaku-tts — 多平台直播弹幕 TTS 朗读器

数据链路:
  抖音 直播间 ──► douyinLive.exe(:1089, 独立实例, 带Cookie) ──► ws://127.0.0.1:1089/ws/<rid>
  B站  直播间 ──► bilibili_client(官方 web WS, 本脚本内置)
                       └──► 统一事件 {platform, type, data} ──► 过滤/去重/连击合并
                             ──► TTS 队列 ──► 语音桥接 :8765 (与 DSH 插件共用) / SAPI 兜底

用法:
  python reader.py                 # 按 config.json 启动已启用平台
  python reader.py --print-only    # 只打印不朗读(测试链路)
  python reader.py --test "你好"   # TTS 链路自检(读一句)
  python reader.py --bili 12345    # 临时指定B站房间号并记住
  python reader.py --douyin 123456789
"""

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from logutil import BOLD, DIM, GREEN, MAGENTA, RED, RESET, YELLOW, CYAN, GRAY, plain, dim_log
from tts_worker import TTSWorker

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
TRAY_SCRIPT = ROOT / "tray.ps1"
TRAY_QUIT = ROOT / "logs" / ".tray-quit"      # 托盘菜单"退出朗读器"写的哨兵文件
CREATE_NO_WINDOW = 0x08000000

DEFAULT_CONFIG = {
    "douyin": {"enabled": True, "room_id": "", "ws_base": "ws://127.0.0.1:1089",
               "auto_start": True, "debug": False},
    "bilibili": {"enabled": False, "room_id": "", "cookie": "", "combo_wait_ms": 1500, "retry_sec": 15},
    "wechat": {"enabled": False, "cdp_port": 9223, "interval_ms": 1200, "debug": False},
    # YouTube: 免 API key(走网页 InnerTube 的 /live_chat)。
    #   video_id 填 11 位 id 或任意观看/Studio 链接(锁定这一场);
    #   channel  填 @handle / UC… / 频道链接(自动找"当前正在直播的那一场", 换了场次不用改配置)
    "youtube": {"enabled": False, "video_id": "", "channel": "", "poll_ms": 0,
                "retry_sec": 20, "discover_sec": 45, "cookie": "", "debug": False},
    "tts": {"provider": "auto", "bridge_base": "http://127.0.0.1:8765", "sapi_voice": "",
            "volume": 1.0, "min_interval_ms": 600, "low_priority_min_interval_ms": 10000,
            "gap_seconds": 5.0, "latest_only": True,
            "max_queue": 8, "timeout_sec": 60,
            "output": "dh", "dh_url": "http://127.0.0.1:8795",
            "dh_min_interval_ms": 10000},
    "read": {
        "comments": True, "gifts": True, "joins": True, "follows": True,
        "likes": True, "guards": True, "superchats": True,
        "vip_gifters": True, "vip_gift_ttl_minutes": 60, "vip_min_diamond": 0,
        "max_chars": 50, "strip_emoji": True, "dedupe_seconds": 10,
        "abbr_nick": True, "drop_words": ["倒立", "翻跟斗"],
        "nick_fallback": ["小可爱", "宝子"],
        "gift_big_min_diamond": 50, "gift_big_on_combo": True,
        "announce_nick": False, "block_users": [], "block_words": [],
        "gift_combo_wait_ms": 1800,
        "templates": {
            "comment": "{content}",
            "gift": "感谢 {nick} 送的 {item}{count}",
            "join": "欢迎 {nick} 进入直播间",
            "follow": "感谢 {nick} 的关注",
            "like": "感谢 {nick} 的点赞",
            "guard": "感谢 {nick} 开通 {item}",
            "superchat": "感谢 {nick} 的醒目留言 {content}",
        },
    },
    "debug": False,
}

EMOJI_RE = re.compile(
    "[\U00010000-\U0010FFFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF"
    "\uFE00-\uFE0F\u200D\u20E3\u00A9\u00AE\u2122]+"
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def clean_text(text: str, strip_emoji: bool = True) -> str:
    text = URL_RE.sub("", text or "")
    if strip_emoji:
        text = EMOJI_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def abbr_nick(nick: str) -> str:
    """朗读用昵称缩写（2026-10-09 用户规则 v2，**不再加"水友"前缀**，省朗读时间）:
      ① 纯中文 ID            → 前 2 个汉字          （阿糖不甜 → 阿糖）
      ② 数字 ID              → 前 4 个数字          （9527123 → 9527）
      ③ 英文 ID              → 第一个英文单词        （Tenzin Trinley → Tenzin）
      ④ 中/英/数/符号 混排    → **从第一个汉字起取 2 个「可读」字符**
                              （汉字 或 ASCII 字母/数字；符号跳过）
                              （一2三 → 一2；魔渡v众生 → 魔渡；ᰔᩚƵHΛПG偉… → 偉）
                              ⚠️ 用户拍板(2026-10-09): 混排用这条 v1 规则 ——
                              「保留紧跟汉字的数字/字母」，比"只读中文"更贴观众 ID 原意
      ⑤ 纯符号(emoji/希腊字母等读不出的) → 返回 ""（由 _speak_nick 用泛称兜底）"""
    n = clean_text(nick)
    if not n:
        return ""
    m = re.search(r"[\u4e00-\u9fff]", n)
    if m:                                    # ①④ 从第一个汉字起取 2 个可读字符
        picked = [ch for ch in n[m.start():]
                  if re.match(r"[\u4e00-\u9fff]", ch) or (ch.isascii() and ch.isalnum())][:2]
        if picked:
            return "".join(picked)
    digits = re.sub(r"\D", "", n)
    if digits:                               # ② 数字(含字母数字混排) → 前 4 个数字
        return digits[:4]
    m2 = re.search(r"[A-Za-z]+", n)          # ③ 英文 → 第一个单词(上限 12 字符)
    if m2:
        return m2.group(0)[:12]
    return ""                                # ⑤ 纯符号 → 交给泛称兜底


def load_config(path: Path) -> dict:
    import copy
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
            _deep_merge(cfg, user)
        except Exception as e:
            print(f"[warn] 配置文件解析失败({e}), 使用默认配置")
    return cfg


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ─────────────────────────── 抖音礼物连击聚合 ───────────────────────────


class DouyinGiftAggregator:
    """按 groupId 聚合连击; repeatEnd==1 或超时收口。"""

    def __init__(self, wait_sec: float, on_flush):
        self.wait = max(0.3, wait_sec)
        self.on_flush = on_flush
        self._entries: dict = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._sweep, name="gift-sweep", daemon=True).start()

    def feed(self, gid: str, nick: str, item: str, num: int, repeat_end: int, value: int = 0):
        now = time.time()
        with self._lock:
            ent = self._entries.get(gid)
            if ent is None:
                ent = {"name": nick, "item": item, "num": num, "value": int(value or 0),
                       "last": now}
                self._entries[gid] = ent
            else:
                ent["num"] = max(ent["num"], num)
                ent["value"] = max(int(ent.get("value") or 0), int(value or 0))   # 单次单价, 不累加
                ent["name"] = nick or ent["name"]
                ent["item"] = item or ent["item"]
                ent["last"] = now
        if repeat_end == 1:
            self.flush(gid)

    def flush(self, gid: str):
        with self._lock:
            ent = self._entries.pop(gid, None)
        if ent:
            self.on_flush(ent["name"], ent["item"], ent["num"], int(ent.get("value") or 0))

    def _sweep(self):
        while not self._stop.wait(0.6):
            now = time.time()
            with self._lock:
                due = [g for g, e in self._entries.items() if now - e["last"] >= self.wait]
            for g in due:
                self.flush(g)

    def stop(self):
        self._stop.set()


# ─────────────────────────── 事件中心 ───────────────────────────


class Reader:
    def __init__(self, cfg: dict, worker: TTSWorker | None):
        self.cfg = cfg
        self.worker = worker
        self.read_cfg = cfg.get("read") or {}
        self._debug = bool(cfg.get("debug"))
        # 打赏过的观众: 昵称 → 最近一次送礼时间(秒)。他们的留言插队优先朗读。
        self._gifters: dict[str, float] = {}
        try:
            self._gifter_ttl = max(60.0, float(self.read_cfg.get("vip_gift_ttl_minutes", 60)) * 60.0)
        except Exception:
            self._gifter_ttl = 3600.0
        t = self.read_cfg.get("templates") or {}
        self.tpl = t
        self._tpl_last: dict = {}      # 多套模板随机时记录上次用的, 避免连续重复
        self._tpl_by_nick: dict = {}   # 同一观众上次用的那套 → 下次不跟他重复
        self.dedupe_seconds = max(1, self.read_cfg.get("dedupe_seconds", 10))
        self._last_read: dict = {}
        self._lock = threading.Lock()
        self._dh_idle = False    # 数字人桥不可达时只提示一次, 恢复后自动重试
        self.aggregator = DouyinGiftAggregator(
            self.read_cfg.get("gift_combo_wait_ms", 1800) / 1000.0,
            self._gift_flush)
        # ---- dh 转发节流(「每隔N秒只读最新弹幕」在 dh 模式的等价实现) ----
        # 本地 TTS 队列的 latest_only/间隔只作用于 worker 播放路径; output=dh 时
        # _speak 直发 _dh_say 完全绕过它 → 弹幕洪峰把桥队列灌满(丢最旧)。
        # 这里按同款语义在转发源头节流: 每平台只留最新一条, 定时轮转发一条;
        # 礼物/打赏观众留言不走池子(立即直发, 与本地队列"事件保序+VIP插队"一致)。
        self._dh_pen: dict[str, tuple] = {}     # platform → (text, tag) 只留最新
        self._dh_pen_lock = threading.Lock()
        self._dh_last = 0.0
        threading.Thread(target=self._dh_flush_loop, daemon=True,
                         name="dh-throttle").start()

    # ---- 语音辅助 ----
    def _should_read(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            last = self._last_read.get(key, 0.0)
            if now - last < self.dedupe_seconds:
                return False
            self._last_read[key] = now
            # 防止字典无限增长
            if len(self._last_read) > 2000:
                cut = now - self.dedupe_seconds * 2
                self._last_read = {k: v for k, v in self._last_read.items() if v >= cut}
            return True

    def _dh_say(self, text: str, priority: str = "normal", extra: dict | None = None):
        """转发给数字人桥 /api/danmaku —— 主播模式: 数字人先复读弹幕, LLM 回答接在后面。
        priority="vip"(打赏过的观众)会被桥侧插队优先播报。
        extra(2026-10-10 新增): answer_only/nick/content/platform ——
          answer_only=True 时桥侧**不复读弹幕**, 只把 LLM 的回答喂给数字人。"""
        t = self.cfg.get("tts") or {}
        base = (t.get("dh_url") or "http://127.0.0.1:8795").rstrip("/")
        body = {"text": text, "priority": priority}
        for k, v in (extra or {}).items():
            if v not in (None, "", False):
                body[k] = v
        try:
            req = urllib.request.Request(
                base + "/api/danmaku",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                resp = json.loads(r.read().decode("utf-8"))
            if resp.get("queued"):
                self._dh_idle = False
                dim_log(f"[DH] 已转发(桥内排队 {resp.get('depth')} 条): {text[:40]}")
            elif not self._dh_idle:
                self._dh_idle = True
                if resp.get("reason") == "idle":
                    plain("[DH] 数字人未连线, 弹幕暂不转发(连线后自动恢复)", YELLOW)
                else:
                    plain(f"[DH] 未入队({resp.get('reason')}), 弹幕暂不转发", YELLOW)
        except Exception as e:  # noqa: BLE001
            if not self._dh_idle:
                self._dh_idle = True
                plain(f"[DH] 转发失败({e}), 数字人桥没起? 弹幕暂不转发", YELLOW)

    def _speak(self, text: str, tag: str, priority: str = "high", extra: dict | None = None):
        text = text.strip()
        limit = self.read_cfg.get("max_chars", 50)
        if limit and len(text) > limit:
            text = text[:limit]
        if self.worker is None:          # --print-only: 只打印
            return
        out = str((self.cfg.get("tts") or {}).get("output", "dh")).lower()
        if out in ("dh", "both"):
            # dh_min_interval_ms <= 0 → 直通转发, 节奏完全交给桥侧(单一节流点, 延迟最低)
            if priority == "vip" or not str(tag).startswith("comment") or self._dh_interval() <= 0:
                self._dh_say(text, priority, extra)   # VIP/礼物事件/直通模式: 立即转发
            else:
                self._dh_enqueue(text, tag, extra)    # 普通弹幕: 节流池(每平台留最新)
        if out in ("bridge", "both") and self.worker:
            self.worker.submit(text, tag, priority)

    def _dh_interval(self) -> float:
        """reader 侧转发间隔(秒)。<=0 = 直通, 由桥侧闸门统一控制节奏。"""
        try:
            return float((self.cfg.get("tts") or {}).get("dh_min_interval_ms", 10000)) / 1000.0
        except Exception:
            return 10.0

    def _dh_enqueue(self, text: str, tag: str, extra: dict | None = None):
        """弹幕进节流池: 覆盖同平台旧条(只留最新), 由 flush 线程按间隔转发。"""
        plat = str(tag).split("|")[1] if "|" in str(tag) else "?"
        with self._dh_pen_lock:
            self._dh_pen[plat] = (text, tag, extra or {})

    def _dh_flush_loop(self):
        """每 dh_min_interval_ms(默认10s) 从池里发一条; 多平台轮转(谁也不饿死);
        睡眠期间新弹幕覆盖同平台旧条 → 永远只读各平台最新。"""
        while True:
            time.sleep(0.5)
            with self._dh_pen_lock:
                if not self._dh_pen:
                    continue
                pen = dict(self._dh_pen)
                self._dh_pen.clear()
            left = self._dh_interval() - (time.time() - self._dh_last)
            if left > 0:
                time.sleep(left)
            with self._dh_pen_lock:             # 睡眠期间新来的 → 取最新
                for plat, item in self._dh_pen.items():
                    pen[plat] = item
                self._dh_pen.clear()
            plat = next(iter(pen))              # 轮转: 每轮发掉队首平台, 其余回池
            item = pen.pop(plat)
            text, _tag = item[0], item[1]
            extra = item[2] if len(item) > 2 else {}
            self._dh_last = time.time()
            with self._dh_pen_lock:
                for p, it in pen.items():
                    self._dh_pen.setdefault(p, it)
            self._dh_say(text, "normal", extra)

    def _blocked(self, nick: str, content: str = "") -> bool:
        users = set(self.read_cfg.get("block_users") or [])
        if nick and nick in users:
            return True
        words = [w for w in (self.read_cfg.get("block_words") or []) if w]
        if words and content:
            low = content.lower()
            if any(w.lower() in low for w in words):
                return True
        return False

    def _speak_nick(self, nick: str) -> str:
        """朗读用昵称(2026-10-09 v2): 按规则缩写, **不加"水友"前缀**;
        纯符号 ID 读不出 → 从 read.nick_fallback 里随机取一个泛称(默认"小可爱/宝子"),
        该列表配成空数组时返回 ""(调用方会退化成"只念内容")。"""
        if not self.read_cfg.get("abbr_nick", True):     # 留个开关: false = 原样念全名
            return (nick or "").strip()
        ab = abbr_nick(nick)
        if ab:
            return ab
        pool = [str(x) for x in (self.read_cfg.get("nick_fallback") or []) if str(x).strip()]
        return random.choice(pool) if pool else ""

    def _pick_tpl(self, kind: str, pool, who: str = "") -> str:
        """从模板池挑一套: ① 不与「全局上一次」重复 ② 也不与「这个人上次」重复
        （观众多的时候更自然）。返回空串 = 池为空/不可用 → 调用方用 fallback。"""
        raw = pool if pool is not None else (self.tpl or {}).get(kind)
        if isinstance(raw, (list, tuple)):
            cand = [str(x) for x in raw if str(x).strip()]
            if not cand:
                return ""
            if len(cand) == 1:
                pick = cand[0]
            else:
                last_all = self._tpl_last.get(kind)
                last_who = self._tpl_by_nick.get(who) if who else None
                ok = [x for x in cand if x != last_all and x != last_who]
                if not ok:
                    ok = [x for x in cand if x != last_all] or cand
                pick = random.choice(ok)
            self._tpl_last[kind] = pick
            if who:
                if len(self._tpl_by_nick) > 2000:        # 简单防膨胀
                    self._tpl_by_nick.clear()
                self._tpl_by_nick[who] = pick
            return pick
        return str(raw) if raw else ""

    def _tpl(self, kind: str, subs: dict, fallback: str, pool=None, who: str = "") -> str:
        """取模板并填值。templates.<kind> 支持:
          · 单个字符串 → 照旧(完全兼容老配置)
          · 字符串数组 → **多套随机挑一套**, 不与上次重复, 也不与「这个人上次」重复
        pool 显式指定时用它（礼物分档: 小额走 templates.gift、大额/连击走 templates.gift_big）。
        可用变量: {nick} {gift} {item} {count} {content}; 未知变量原样保留。
        """
        raw = self._pick_tpl(kind, pool, who) or fallback
        text = str(raw)
        for k, v in (subs or {}).items():
            text = text.replace("{" + k + "}", str(v))
        return text

    def _gift_phrase(self, item: str, num: int) -> str:
        """礼物短语（用户 2026-10-09 拍板）: **数量 + 个 + 礼物名**，不要念成「小心心 x3」。
          单个   → 只说礼物名          （小心心）
          2-99   → 「N个礼物名」        （3个小心心）
          100+   → 「一大堆礼物名」      （数量太大念着累, 而且更可爱）"""
        n = max(int(num or 1), 1)
        if n <= 1:
            return item
        if n <= 99:
            return f"{n}个{item}"
        return f"一大堆{item}"

    def _voice_gift(self, nick: str, item: str, num: int, value: int = 0):
        """礼物感谢播报。分档(用户 2026-10-09):
          · 小额单个   → templates.gift      （1-10 那类温和版）
          · 大额/连击  → templates.gift_big  （11/14/17-19 那类夸张版）
            大额 = 单价 ≥ read.gift_big_min_diamond 钻; 连击 = 数量 ≥ 2(read.gift_big_on_combo)
          gift_big 没配/为空 → 自动回落到 gift 池（老配置照样能用）"""
        if not self.read_cfg.get("gifts", True):
            return
        item = (str(item) or "").strip() or "礼物"      # 个别事件不带礼物名 → 别念成窟窿
        count_txt = ""                                  # {count} = 旧写法(" x3"), 保留兼容
        if num > 1:
            count_txt = f" x{num}" if num <= 99 else " 一大波"
        pool = None
        try:
            big = False
            if self.read_cfg.get("gift_big_on_combo", True) and int(num or 1) >= 2:
                big = True
            need = int(self.read_cfg.get("gift_big_min_diamond", 50) or 0)
            if need > 0 and int(value or 0) >= need:
                big = True
            if big:
                bp = (self.tpl or {}).get("gift_big")
                if isinstance(bp, (list, tuple)) and [x for x in bp if str(x).strip()]:
                    pool = bp
        except Exception:
            pool = None
        text = self._tpl("gift", {"nick": self._speak_nick(nick),
                                  "gift": self._gift_phrase(item, num),
                                  "item": item, "count": count_txt},
                         "感谢 {nick} 送的 {item}{count}", pool=pool, who=nick or "?")
        # 2026-10-19 桥侧「礼物合并感谢」: 把礼物做成结构化事件带过去(kind=gift),
        # 上面这句模板文案退化成"合并关闭 / LLM 失败时的兜底"; say_nick 是已缩写好的念法。
        self._speak(text, "gift", extra={
            "kind": "gift", "gift_item": item, "gift_num": int(num or 1),
            "gift_value": int(value or 0), "say_nick": self._speak_nick(nick),
            "nick": nick, "content": text})

    def _gift_flush(self, nick: str, item: str, num: int, value: int = 0):
        self._voice_gift(nick, item, num, value)

    # ---- 事件入口 ----
    def on_event(self, ev: dict):
        try:
            platform = ev.get("platform") or "?"
            etype = ev.get("type") or ""
            d = ev.get("data") or {}
            handler = getattr(self, f"_ev_{etype.lower()}", None)
            if handler:
                handler(platform, d)
        except Exception as e:
            dim_log(f"[事件处理异常] {e}")

    def _ev_chat(self, platform: str, d: dict):
        """观众留言(弹幕)。
        2026-10-10 用户拍板: 关闭托盘「朗读哪些 → 观众留言(弹幕)」不再等于"丢弃弹幕"——
        弹幕照样转发给数字人桥, 只是**不复读内容、只把 LLM 的回答喂给数字人**
        (answer_only=True + 带观众名/内容/平台, 让 LLM 知道这是直播间弹幕)。"""
        from logutil import event_line
        nick, content = d.get("name") or "", d.get("content") or ""
        event_line(platform, "💬", "Chat", f"{BOLD}{nick}{RESET}: {content}")
        if not nick or not content:
            return
        read_aloud = bool(self.read_cfg.get("comments", True))
        # 敏感词丢弃(2026-10-09): 触审词被 TTS 念出会触发 Vidu 音频审核 → 服务端强制挂断整场。
        # 只答不念时同样要丢: LLM 很可能把原话复述出来, 一样会踩审核。
        drops = [w for w in (self.read_cfg.get("drop_words") or []) if w]
        if drops and any(w in content for w in drops):
            event_line(platform, "🚫", "Drop", f"命中敏感词丢弃: {content[:40]}")
            return
        if self._blocked(nick, content):
            return
        key = f"chat|{platform}|{nick}|{content}"
        if not self._should_read(key):
            return
        tpl = self.tpl.get("comment") or "{content}"
        speak_nick = self._speak_nick(nick)
        if speak_nick:
            text = tpl.replace("{nick}", speak_nick).replace("{content}", content)
            if self.read_cfg.get("announce_nick") and tpl == "{content}":
                text = f"{speak_nick}说{content}"
        else:
            # 纯符号 ID 且泛称列表为空 → 不念昵称, 也不留"说"这个孤零零的字
            text = content
        extra = {"answer_only": (not read_aloud),
                 # 给 LLM 的称呼用**缩写后**的名字(阿糖), 否则它可能照念"阿糖不甜"这种长 ID
                 "nick": speak_nick or "", "content": content, "platform": platform}
        # 打赏过的观众(默认 60 分钟内): 留言插队 + 强制朗读(不受"只读最新"丢弃与朗读间隔限制)
        if self.read_cfg.get("vip_gifters", True) and self._is_vip(nick):
            event_line(platform, "💎", "VipChat", f"{BOLD}{nick}{RESET}: {content}")
            self._speak(text, f"vip|comment|{platform}", priority="vip", extra=extra)
            return
        self._speak(text, f"comment|{platform}", extra=extra)

    def _is_vip(self, nick: str) -> bool:
        """该观众是否在 TTL 内送过礼物(送过 → 留言优先朗读)。"""
        if not nick:
            return False
        ts = self._gifters.get(nick)
        if not ts:
            return False
        if time.time() - ts > self._gifter_ttl:
            self._gifters.pop(nick, None)
            return False
        return True

    def _gift_value(self, platform: str, d: dict) -> int:
        """把各平台的礼物价值折算成"抖币"(1 元 ≈ 10 钻), 用于 VIP 门槛判断。"""
        if platform == "抖音":
            return max(0, int(d.get("diamond") or 0))
        # B站: 客户端已折算成元
        try:
            return max(0, int(round(float(d.get("price_yuan") or 0) * 10)))
        except Exception:
            return 0

    def _ev_gift(self, platform: str, d: dict):
        from logutil import event_line
        nick, item = d.get("name") or "", d.get("item") or ""
        num = max(int(d.get("num") or 1), 1)
        if nick:
            # 登记为"打赏过的观众"(留言可插队优先朗读), 受价值门槛限制
            try:
                need = int(self.read_cfg.get("vip_min_diamond", 0) or 0)
            except Exception:
                need = 0
            val = self._gift_value(platform, d)
            if val >= need:
                self._gifters[nick] = time.time()
                if self._debug:
                    from logutil import dim_log as _dl
                    _dl(f"[VIP] 登记打赏观众: {nick} ({val}钻 ≥ 门槛{need})")
        if platform == "抖音":
            combo = int(d.get("repeat_end") or 0) != 1
            suffix = DIM + " (连击中)" + RESET if combo else ""
            event_line(platform, "🎁", "Gift",
                       f"{nick} 赠送 {item} x{num}{suffix}")
            gid = d.get("group_id") or f"{nick}|{item}"
            self.aggregator.feed(gid, nick, item, num, int(d.get("repeat_end") or 0),
                                 self._gift_value(platform, d))
        else:  # B站: 已在客户端聚合好
            yuan = float(d.get("price_yuan") or 0)
            extra = DIM + f" (≈{yuan:g}元)" + RESET if yuan >= 1 else ""
            event_line(platform, "🎁", "Gift", f"{nick} 赠送 {item} x{num}{extra}")
            self._voice_gift(nick, item, num, self._gift_value(platform, d))

    def _ev_enterroom(self, platform: str, d: dict):
        from logutil import event_line
        nick = d.get("name") or ""
        event_line(platform, "👋", "EnterRoom", nick)
        if self.read_cfg.get("joins", True) and nick and self._should_read(f"join|{platform}|{nick}"):
            self._speak(self._tpl("join", {"nick": self._speak_nick(nick)},
                                  "欢迎 {nick} 进入直播间"), "join", priority="low")

    def _ev_follow(self, platform: str, d: dict):
        from logutil import event_line
        nick = d.get("name") or ""
        event_line(platform, "⭐", "Follow", nick)
        if self.read_cfg.get("follows", True) and nick and self._should_read(f"follow|{platform}|{nick}"):
            self._speak(self._tpl("follow", {"nick": self._speak_nick(nick)},
                                  "感谢 {nick} 的关注"), "follow")

    def _ev_like(self, platform: str, d: dict):
        from logutil import event_line
        nick = d.get("name") or ""
        cnt = max(int(d.get("count") or 1), 1)
        event_line(platform, "👍", "Like", f"{nick} x{cnt}")
        if self.read_cfg.get("likes", True) and nick and self._should_read(f"like|{platform}|{nick}"):
            self._speak(self._tpl("like", {"nick": self._speak_nick(nick), "count": f" x{cnt}"},
                                  "感谢 {nick} 的点赞"), "like", priority="low")

    def _ev_subscribe(self, platform: str, d: dict):
        from logutil import event_line
        nick, item = d.get("name") or "", d.get("item") or "大航海"
        num = max(int(d.get("num") or 1), 1)
        event_line(platform, "🛡️", "Guard", f"{nick} 开通 {item} ×{num}")
        if self.read_cfg.get("guards", True) and nick:
            if not self._should_read(f"guard|{platform}|{nick}|{item}|{num}"):
                return
            self._speak(self._tpl("guard", {"nick": self._speak_nick(nick), "item": item,
                                            "gift": item},
                                  "感谢 {nick} 开通 {item}"), "guard")

    def _ev_superchat(self, platform: str, d: dict):
        from logutil import event_line
        nick, content = d.get("name") or "", clean_text(d.get("content") or "")
        price = float(d.get("price") or 0)
        event_line(platform, "💰", "SuperChat",
                   f"{BOLD}{nick}{RESET} ¥{price:g}: {content}")
        if self.read_cfg.get("superchats", True) and nick:
            self._speak(self._tpl("superchat", {"nick": self._speak_nick(nick), "content": content},
                                  "感谢 {nick} 的醒目留言 {content}"), "superchat")

    def _ev_endlive(self, platform: str, d: dict):
        from logutil import event_line
        event_line(platform, "📴", "EndLive", "直播结束(保持连接等待)")


# ─────────────────────────── 主流程 ───────────────────────────


def _persist_config(cfg: dict):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ─────────────── 运行时音量控制(控制台命令 / 配置文件热加载) ───────────────
# 目的: 直播中觉得 TTS 太响/太轻, 不用退出重开 ——
#   ① 在朗读器窗口里直接敲命令:  vol / vol 0.3 / vol -10% / vol up / vol mute
#   ② 或者在编辑器里改 config.json → tts.volume, 2 秒内自动生效

VOL_STEP = 0.05

# 可运行时开关的朗读项(对应 config.json → read.*)
READ_FLAGS = ("comments", "gifts", "joins", "follows", "likes", "guards", "superchats", "vip_gifters")
READ_NAMES = {
    "comments": "念弹幕内容",   # 关闭 = 不复读, 只让 LLM 回应(2026-10-10)
    "gifts": "礼物",
    "joins": "进场",
    "follows": "关注",
    "likes": "点赞",
    "guards": "舰长/守护",
    "superchats": "醒目留言",
    "vip_gifters": "礼物观众留言优先",
}

VOL_HELP = (
    "可随时输入的命令(立即生效并写回 config.json):\n"
    "  ── 朗读节奏 ──\n"
    "  gap                        看当前间隔(读完一条后隔多久再读下一条)\n"
    "  gap 8                      改成 8 秒     gap off（=0）关闭间隔\n"
    "  latest on / latest off     等待期间弹幕只读最新一条(默认 on)\n"
    "  ── 朗读开关 ──\n"
    "  read                       看各项开关状态\n"
    "  read comments off          不再朗读观众发言(弹幕)\n"
    "  read comments on           恢复朗读观众发言\n"
    "  read gifts off / on        礼物(其余: joins 进场 / follows 关注\n"
    "                             likes 点赞 / guards 舰长 / superchats 醒目留言)\n"
    "  comments off               等价简写(直接写项名)\n"
    "  ── 音量 ──\n"
    "  vol                        看当前音量\n"
    "  vol 0.3                    设为 0.3（取值 0~1）\n"
    "  vol +0.05 / -0.05          在当前值上加减\n"
    "  vol +10% / -10%            按比例增减\n"
    "  vol up / down              每次 ±0.05\n"
    "  vol mute                   静音(0)      vol reset  回到 0.5\n"
    "  ── 其它 ──\n"
    "  help                       这条帮助      quit/q     退出朗读器"
)


def _persist_tts(key: str, val) -> None:
    """只把 tts.<key> 写回 config.json（保留其它字段原样）。"""
    try:
        raw = {}
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("tts", {})[key] = val
        CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        plain(f"[warn] 写回 config.json 失败: {e}", YELLOW)


def apply_gap_cmd(worker, cfg: dict, line: str) -> str | None:
    """解析 'gap ...' / 'latest ...' 命令; 不是这两类则返回 None(交给其它处理器)。"""
    raw = (line or "").strip().lower()
    if not raw:
        return None
    parts = raw.split()
    cmd = parts[0]

    if cmd in ("gap", "间隔"):
        t = cfg.setdefault("tts", {})
        cur = float(getattr(worker, "gap", t.get("gap_seconds", 5.0))) if worker is not None \
            else float(t.get("gap_seconds", 5.0))
        latest = bool(getattr(worker, "latest_only", t.get("latest_only", True))) if worker is not None \
            else bool(t.get("latest_only", True))
        if len(parts) == 1:
            return (f"朗读间隔: 每条读完隔 {cur:g} 秒再读下一条"
                    f"（等待期间只读最新弹幕: {'开' if latest else '关'}）\n"
                    f"  改: gap 8 / gap off     只读最新: latest off")
        arg = parts[1].replace('s', '').replace('秒', '')
        if arg in ("off", "no", "关"):
            new = 0.0
        else:
            try:
                new = float(arg)
            except Exception:
                return f"看不懂: {parts[1]}（示例: gap 5 / gap 8 / gap off）"
        new = max(0.0, min(120.0, new))
        if worker is not None:
            worker.set_gap(new)
        t["gap_seconds"] = round(new, 2)
        _persist_tts("gap_seconds", round(new, 2))
        tail = "（已关闭间隔：来一条读一条）" if new <= 0 else "（读完一条后等这么久，再念那时最新的一条）"
        return f"朗读间隔: {cur:g} → {new:g} 秒{tail}，已写入 config.json 并立即生效"

    if cmd in ("latest", "最新"):
        t = cfg.setdefault("tts", {})
        cur = bool(getattr(worker, "latest_only", t.get("latest_only", True))) if worker is not None \
            else bool(t.get("latest_only", True))
        if len(parts) == 1:
            return f"等待期间只读最新弹幕: {'开' if cur else '关'}（latest on / latest off）"
        v = parts[1]
        if v in ("on", "1", "true", "yes", "开"):
            new = True
        elif v in ("off", "0", "false", "no", "关"):
            new = False
        else:
            return f"看不懂: {v}（用 on / off）"
        if worker is not None:
            worker.set_latest_only(new)
        t["latest_only"] = new
        _persist_tts("latest_only", new)
        tail = "（积压的旧弹幕丢弃，只念最新那条）" if new else "（积压的弹幕按顺序一条条念完）"
        return f"等待期间只读最新弹幕: {'开' if new else '关'}{tail}，已写入 config.json 并立即生效"

    return None


def _sync_tts_knobs(worker, cfg: dict, disk: dict) -> list[str]:
    """把磁盘上的 tts.gap_seconds / tts.latest_only 同步进运行中的 worker（不用重启）。"""
    changed = []
    dt = disk.get("tts") or {}
    if worker is None or not isinstance(dt, dict):
        return changed
    if "gap_seconds" in dt:
        try:
            v = max(0.0, min(120.0, float(dt["gap_seconds"])))
            if abs(v - float(getattr(worker, "gap", 5.0))) > 1e-6:
                worker.set_gap(v)
                cfg.setdefault("tts", {})["gap_seconds"] = round(v, 2)
                changed.append(f"朗读间隔={v:g}s")
        except Exception:
            pass
    if "latest_only" in dt:
        v = bool(dt["latest_only"])
        if v != bool(getattr(worker, "latest_only", True)):
            worker.set_latest_only(v)
            cfg.setdefault("tts", {})["latest_only"] = v
            changed.append(f"只读最新={'开' if v else '关'}")
    return changed


def _persist_volume(v: float) -> None:
    """只把 tts.volume 写回 config.json(保留文件里其它字段原样, 不整份重写)。"""
    try:
        raw = {}
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("tts", {})["volume"] = round(float(v), 3)
        CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        plain(f"[warn] 音量写回 config.json 失败: {e}", YELLOW)


def _persist_read_flag(key: str, val: bool) -> None:
    """只把 read.<key> 写回 config.json。"""
    try:
        raw = {}
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("read", {})[key] = bool(val)
        CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        plain(f"[warn] 开关写回 config.json 失败: {e}", YELLOW)


def _read_flags_text(cfg: dict) -> str:
    rd = cfg.get("read") or {}
    items = [f"{READ_NAMES[k]}={'开' if rd.get(k, True) else '关'}" for k in READ_FLAGS]
    return "朗读开关: " + "  ".join(items) + "   （例: read comments off）"


def apply_read_cmd(cfg: dict, line: str) -> str | None:
    """解析一行朗读开关命令。不是开关命令(交给音量命令处理)时返回 None。

    支持: read / read comments off / comments off / read gifts on ...
    """
    raw = (line or "").strip().lower()
    if not raw:
        return None
    parts = raw.split()
    first = parts[0]
    if first in ("read", "say", "朗读"):
        if len(parts) == 1:
            return _read_flags_text(cfg)
        target = parts[1]
        val = parts[2] if len(parts) > 2 else None
    elif first in READ_FLAGS:
        target, val = first, (parts[1] if len(parts) > 1 else None)
    else:
        return None

    if target not in READ_FLAGS:
        return f"没有这一项: {target}（可开关: {', '.join(READ_FLAGS)}）"

    rd = cfg.setdefault("read", {})
    if val is None:                      # 不带参数 = 取反
        new = not bool(rd.get(target, True))
    elif val in ("on", "1", "true", "yes", "开", "开启"):
        new = True
    elif val in ("off", "0", "false", "no", "关", "关闭"):
        new = False
    else:
        return f"看不懂: {val}（用 on / off）"

    rd[target] = new
    _persist_read_flag(target, new)
    return f"朗读【{READ_NAMES[target]}】已{'开启' if new else '关闭'}（已写入 config.json，立即生效）"


def _sync_read_flags(cfg: dict, disk: dict) -> list[str]:
    """把磁盘上的 read.* 开关同步进内存 —— 就地改 cfg["read"](Reader 持有它的引用), 不用重启。"""
    changed = []
    disk_read = disk.get("read") or {}
    if not isinstance(disk_read, dict):
        return changed
    mem = cfg.setdefault("read", {})
    for k in READ_FLAGS:
        if k in disk_read and bool(disk_read[k]) != bool(mem.get(k, True)):
            mem[k] = bool(disk_read[k])
            changed.append(f"{READ_NAMES[k]}={'开' if mem[k] else '关'}")
    return changed


def start_tray(cfg_path: Path):
    """拉起托盘图标(PowerShell + WinForms, 无需第三方库)。

    托盘只读写 config.json —— 本脚本的热加载会在 2 秒内把改动应用上去, 所以不需要任何 IPC。
    """
    try:
        if sys.platform != "win32" or not TRAY_SCRIPT.is_file():
            return None
        exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
        p = subprocess.Popen(
            [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
             "-File", str(TRAY_SCRIPT), "-Config", str(cfg_path)],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        plain(f"托盘图标已启动（右下角通知区域找紫色音量图标；右键调音量/开关，PID {p.pid}）", GREEN)
        return p
    except Exception as e:
        plain(f"[warn] 托盘启动失败(不影响朗读): {e}", YELLOW)
        return None


def apply_volume_cmd(worker, cfg: dict, line: str) -> str:
    """解析一行音量命令 → 立即改 worker.volume 并落盘。返回给用户看的提示文本。"""
    raw = (line or "").strip()
    if not raw:
        return ""
    parts = raw.split()
    cmd = parts[0].lower()
    if cmd in ("help", "?", "h"):
        return VOL_HELP
    if cmd not in ("vol", "v", "volume"):
        return f"未知命令: {raw}（输入 help 看用法；音量命令是 vol）"

    cur = float(worker.volume) if worker is not None else float((cfg.get("tts") or {}).get("volume", 0.0))
    if len(parts) == 1:
        return f"当前音量: {cur:.2f}（vol 0.3 设置 / vol -10% 调低 / vol up 调高）"

    arg = parts[1].lower()
    try:
        if arg in ("up", "+"):
            new = cur + VOL_STEP
        elif arg in ("down", "-"):
            new = cur - VOL_STEP
        elif arg == "mute":
            new = 0.0
        elif arg == "reset":
            new = 0.5
        elif arg.endswith("%"):
            new = cur * (1 + float(arg[:-1]) / 100.0)
        elif arg.startswith(("+", "-")):
            new = cur + float(arg)
        else:
            new = float(arg)
    except Exception:
        return f"看不懂的音量值: {parts[1]}（示例: vol 0.3 / vol -10% / vol +0.05）"

    new = max(0.0, min(1.0, new))
    if worker is not None:
        worker.set_volume(new)
    cfg.setdefault("tts", {})["volume"] = round(new, 3)
    _persist_volume(new)
    arrow = "调低" if new < cur else ("调高" if new > cur else "不变")
    return f"音量 {cur:.2f} → {new:.2f}（{arrow}，已写入 config.json 并立即生效）"


def console_loop(worker, cfg: dict, stop_evt: threading.Event):
    """控制台命令线程: 只有真的在终端窗口里跑才启用(管道/重定向时自动跳过)。"""
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return
    except Exception:
        return
    plain("可随时调: 输入 help 看全部命令（read comments off = 不朗读观众发言；vol 0.3 = 音量）", GRAY)
    while not stop_evt.is_set():
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            return
        except Exception:
            return
        if (line or "").strip().lower() in ("quit", "q", "exit"):
            plain("收到退出命令, 正在关闭…", YELLOW)
            stop_evt.set()
            return
        try:
            msg = apply_gap_cmd(worker, cfg, line)
        except Exception as e:
            msg = f"命令执行失败: {type(e).__name__}: {e}"
        if msg is None:
            try:
                msg = apply_read_cmd(cfg, line)
            except Exception as e:
                msg = f"命令执行失败: {type(e).__name__}: {e}"
        if msg is None:
            try:
                msg = apply_volume_cmd(worker, cfg, line)
            except Exception as e:
                msg = f"命令执行失败: {type(e).__name__}: {e}"
        if msg:
            plain(msg, GREEN)


def config_watch_loop(worker, cfg: dict, stop_evt: threading.Event, path: Path):
    """配置热加载: 在编辑器里改 config.json（tts.volume / read.* 开关）2 秒内自动生效。

    同时兼任"托盘菜单请求退出"的哨兵检查(托盘写 logs\\.tray-quit)。
    """
    try:
        last = path.stat().st_mtime
    except Exception:
        last = 0.0
    while not stop_evt.is_set():
        time.sleep(2)
        # 托盘菜单 → 退出朗读器
        try:
            if TRAY_QUIT.exists():
                TRAY_QUIT.unlink()
                plain("托盘菜单请求退出朗读器, 正在关闭…", YELLOW)
                stop_evt.set()
                return
        except Exception:
            pass
        try:
            m = path.stat().st_mtime
        except Exception:
            continue
        if m == last:
            continue
        try:
            disk = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            # 可能正好读到托盘/编辑器写文件的中间态(截断后还没写完):
            # 此时**不能**推进 last, 否则这次改动会被永久跳过(要等下一次写才恢复)。
            continue
        if not isinstance(disk, dict):
            continue
        last = m          # 只在解析成功后推进, 保证任何一次改动都不会丢
        # 音量
        try:
            v = float((disk.get("tts") or {}).get("volume"))
            if worker is not None and abs(v - float(worker.volume)) > 1e-6:
                worker.set_volume(v)
                cfg.setdefault("tts", {})["volume"] = round(worker.volume, 3)
                plain(f"检测到配置文件改动: 音量 → {worker.volume:.2f}（下一条生效）", GREEN)
        except Exception:
            pass
        # 朗读开关(comments/gifts/joins/...)
        changed = _sync_read_flags(cfg, disk)
        if changed:
            plain("检测到配置文件改动: 朗读开关 " + ", ".join(changed), GREEN)
        # 朗读节奏(间隔 / 只读最新)
        changed2 = _sync_tts_knobs(worker, cfg, disk)
        if changed2:
            plain("检测到配置文件改动: " + ", ".join(changed2), GREEN)
        # 数字人出口(output: dh|bridge|both / dh_url)
        try:
            t = disk.get("tts") or {}
            tc = cfg.setdefault("tts", {})
            msgs = []
            out = str(t.get("output", "")).strip().lower()
            if out in ("dh", "bridge", "both") and out != tc.get("output"):
                tc["output"] = out
                msgs.append(f"output → {out}")
            url = str(t.get("dh_url", "")).strip()
            if url and url != tc.get("dh_url"):
                tc["dh_url"] = url
                msgs.append("dh_url 已更新")
            if msgs:
                plain("检测到配置文件改动: " + ", ".join(msgs), GREEN)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="多平台弹幕 TTS 朗读器")
    ap.add_argument("--no-tray", action="store_true", help="不启动托盘图标")
    ap.add_argument("--print-only", action="store_true", help="只打印事件, 不朗读")
    ap.add_argument("--test", metavar="TEXT", help="TTS 链路自检: 朗读指定文本后退出")
    ap.add_argument("--debug", action="store_true", help="调试输出(未知命令/事件)")
    ap.add_argument("--douyin", metavar="RID", help="覆盖抖音房间号并记住")
    ap.add_argument("--bili", metavar="RID", help="覆盖B站房间号并记住(同时启用B站)")
    ap.add_argument("--yt", metavar="URL或ID", help="覆盖YouTube直播 videoId/链接并记住(同时启用YouTube)")
    args = ap.parse_args()

    cfg = load_config(CONFIG_PATH)
    # 控制台输出同时落盘: 窗口被 ✕ 关掉 / 异常退出后, logs\reader.log 里还能看到当时发生了什么
    try:
        import logutil
        logutil.set_logfile(ROOT / "logs" / "reader.log")
    except Exception:
        pass
    if args.debug:
        cfg["debug"] = True
        cfg["douyin"]["debug"] = True
        cfg["bilibili"]["debug"] = True
    changed = False
    if args.douyin:
        cfg["douyin"]["room_id"] = str(args.douyin)
        cfg["douyin"]["enabled"] = True
        changed = True
    if args.bili:
        cfg["bilibili"]["room_id"] = str(args.bili)
        cfg["bilibili"]["enabled"] = True
        changed = True
    if args.yt:
        cfg.setdefault("youtube", {})
        cfg["youtube"]["video_id"] = str(args.yt).strip()
        cfg["youtube"]["enabled"] = True
        changed = True
    if changed:
        _persist_config(cfg)

    print(f"{BOLD}多平台弹幕朗读器 (multi-danmaku-tts){RESET}")

    # ---- TTS 自检 ----
    if args.test:
        worker = TTSWorker(cfg)
        engine, wav = worker._synth(args.test)
        if wav:
            # 自检也必须走音量缩放, 否则"用 --test 试音量"永远是满音量,
            # 会让人误判成"托盘调音量没生效"。
            if worker.volume < 1.0:
                wav = worker._scale_wav(wav, worker.volume)
            import winsound
            winsound.PlaySound(wav, winsound.SND_MEMORY)
            plain(f"TTS 自检完成 (engine={engine}, 音量 {worker.volume:.2f})", GREEN)
        else:
            plain("TTS 自检失败: 无音频输出", RED)
        return

    # ---- 启动平台 ----
    worker = None
    if args.print_only:
        plain("--print-only 模式: 不朗读, 只打印", YELLOW)
    else:
        worker = TTSWorker(cfg)
        worker.start()

    reader = Reader(cfg, worker)
    clients = []

    dy = cfg["douyin"]
    if dy.get("enabled", True) and str(dy.get("room_id") or "").strip():
        from douyin_client import DouyinClient
        dc = DouyinClient(str(dy["room_id"]).strip(), reader.on_event, dy)
        if dy.get("auto_start", True):
            dc.start_service()
        dc.start()
        clients.append(dc)
    else:
        plain("抖音平台未启用或未配置房间号 (config.json → douyin)", GRAY)

    bili = cfg["bilibili"]
    if bili.get("enabled") and str(bili.get("room_id") or "").strip():
        from bilibili_client import BilibiliClient
        bc = BilibiliClient(str(bili["room_id"]).strip(), reader.on_event, bili)
        bc.start()
        clients.append(bc)
    else:
        plain("B站平台未启用。开通方法: python reader.py --bili <B站直播间号> "
              "或编辑 config.json → bilibili", GRAY)

    wx = cfg.get("wechat") or {}
    if wx.get("enabled"):
        from wechat_client import WechatConsoleClient
        wc = WechatConsoleClient(cfg, reader.on_event)
        wc.start()
        clients.append(wc)
    else:
        plain("视频号未启用 (config.json → wechat.enabled)", GRAY)

    yt = cfg.get("youtube") or {}
    yt_target = str(yt.get("video_id") or "").strip() or str(yt.get("channel") or "").strip()
    if yt.get("enabled") and yt_target:
        from youtube_client import YouTubeClient
        yc = YouTubeClient(yt_target, reader.on_event, yt)
        yc.start()
        clients.append(yc)
    elif yt.get("enabled"):
        plain("YouTube 已启用但没填目标 (config.json → youtube.video_id 填链接/11位id, "
              "或 youtube.channel 填 @handle/UC… 自动找当前直播)", YELLOW)
    else:
        plain("YouTube 未启用 (python reader.py --yt <直播链接或频道> 或 config.json → youtube)", GRAY)

    if not clients:
        plain("没有任何平台在运行, 退出。", RED)
        return

    plain("按 Ctrl+C 退出。提示: 同时只运行一个朗读器, 避免双份语音。", GRAY)
    stop_evt = threading.Event()
    tray = None
    if worker:
        # 清掉上次残留的"托盘请求退出"哨兵, 否则新实例会立刻退出
        try:
            if TRAY_QUIT.exists():
                TRAY_QUIT.unlink()
        except Exception:
            pass
        # 音量运行时可调: 控制台命令 + 配置文件热加载
        threading.Thread(target=console_loop, args=(worker, cfg, stop_evt),
                         name="console", daemon=True).start()
        threading.Thread(target=config_watch_loop, args=(worker, cfg, stop_evt, CONFIG_PATH),
                         name="cfgwatch", daemon=True).start()
        if not args.no_tray:
            tray = start_tray(CONFIG_PATH)
    try:
        while not stop_evt.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if tray is not None:
            try:
                tray.terminate()
                plain("已关闭托盘图标", GRAY)
            except Exception:
                pass
        plain("正在退出…", GRAY)
        for c in clients:
            c.stop.set()
        reader.aggregator.stop()
        if worker:
            worker.shutdown()
        for c in clients:
            if isinstance(c, object) and getattr(c, "_proc", None):
                try:
                    c._proc.terminate()
                    c._proc.wait(timeout=5)
                    plain("已停止 douyinLive 服务实例", GRAY)
                except Exception:
                    pass


if __name__ == "__main__":
    main()
