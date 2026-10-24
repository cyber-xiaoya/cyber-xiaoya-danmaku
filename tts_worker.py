#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""tts_worker.py — 朗读队列 worker(与旧项目同语义, 独立实现)。

优先级 (config.tts.provider = auto):
  1. 本机语音桥接  http://127.0.0.1:8765/api/tts  (DSH 语音插件共用, 只调用不改)
  2. 回退 Windows SAPI (系统自带, sapi_tts.ps1)
"""

import array
import io
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import wave
import winsound
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
SAPI_PS1 = ROOT / "sapi_tts.ps1"

CREATE_NO_WINDOW = 0x08000000


class TTSWorker(threading.Thread):
    def __init__(self, cfg: dict):
        super().__init__(name="tts-worker", daemon=True)
        t = cfg.get("tts") or {}
        self.provider = t.get("provider", "auto")
        self.bridge_base = t.get("bridge_base", "http://127.0.0.1:8765").rstrip("/")
        self.sapi_voice = t.get("sapi_voice", "")
        self.min_interval = max(0, t.get("min_interval_ms", 600)) / 1000.0
        self.low_interval = max(0, t.get("low_priority_min_interval_ms", 10000)) / 1000.0
        self.timeout = t.get("timeout_sec", 60)
        self.volume = max(0.0, min(1.0, float(t.get("volume", 1.0))))
        # 朗读间隔: 读完一条 → 至少隔 gap_seconds 秒 → 再读"那时最新的一条"
        self.gap = max(0.0, float(t.get("gap_seconds", 5.0)))
        # 等待期间积压的弹幕只读最新一条(旧的丢弃); 礼物/关注等事件不丢, 按顺序读
        self.latest_only = bool(t.get("latest_only", True))
        self.max_queue = max(1, t.get("max_queue", 8))
        self.q: queue.Queue = queue.Queue(maxsize=self.max_queue)          # 高优先级
        self.low_q: queue.Queue = queue.Queue(maxsize=4)                    # 低优先级(欢迎/点赞)
        self.vip_q: queue.Queue = queue.Queue(maxsize=4)                    # VIP: 打赏过的观众留言
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._bridge_ok: bool | None = None
        self._bridge_retry_at = 0.0
        self._last_low_play = 0.0
        self.current = ""

    # ---- 队列 ----
    def submit(self, text: str, tag: str = "", priority: str = "high") -> bool:
        text = (text or "").strip()
        if not text:
            return False
        if priority == "vip":
            # VIP(打赏过的观众留言): 独立队列, 永远插队, 不受"只读最新"影响; 满了丢最旧的 VIP
            try:
                self.vip_q.put_nowait((text, tag))
                return True
            except queue.Full:
                try:
                    self.vip_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.vip_q.put_nowait((text, tag))
                    return True
                except queue.Full:
                    return False
        q = self.q if priority == "high" else self.low_q
        try:
            q.put_nowait((text, tag))
            return True
        except queue.Full:
            # 队列满: 腾位置而不是直接丢新弹幕 —— 否则小平台(视频号/B站)
            # 会被大平台(抖音)的洪水永远拒之门外。
            if self._drop_one_comment(tag):
                try:
                    q.put_nowait((text, tag))
                    return True
                except queue.Full:
                    return False
            return False

    def _drop_one_comment(self, incoming_tag: str) -> bool:
        """从队列里丢掉一条旧弹幕腾位置。优先丢与来件同平台的旧弹幕。

        返回 True 表示成功腾出至少一个位置。
        """
        drained = []
        while True:
            try:
                drained.append(self.q.get_nowait())
            except queue.Empty:
                break
        if not drained:
            return False
        comments = [i for i in drained if str(i[1]).startswith("comment")]
        if not comments:
            # 没有弹幕可丢: 原样放回
            for it in drained:
                try:
                    self.q.put_nowait(it)
                except queue.Full:
                    break
            return False
        same = [i for i in comments if i[1] == incoming_tag]
        victim = same[0] if same else comments[0]
        dropped = False
        for it in drained:
            if not dropped and it is victim:
                dropped = True
                continue          # 丢掉它
            try:
                self.q.put_nowait(it)
            except queue.Full:
                break
        return dropped

    def shutdown(self):
        self.stop.set()
        try:
            winsound.PlaySound(None, 0)
        except Exception:
            pass

    # ---- 音量(运行时可调, 不用重启) ----
    def set_volume(self, v: float) -> float:
        """改音量(0.0~1.0), 返回实际生效值。只赋值一个 float, 线程安全。"""
        try:
            v = max(0.0, min(1.0, float(v)))
        except Exception:
            return self.volume
        self.volume = v
        return self.volume

    # ---- 朗读间隔 / 只读最新(运行时可调) ----
    def set_gap(self, seconds: float) -> float:
        try:
            self.gap = max(0.0, min(120.0, float(seconds)))
        except Exception:
            pass
        return self.gap

    def set_latest_only(self, on: bool) -> bool:
        self.latest_only = bool(on)
        return self.latest_only

    @staticmethod
    def _newest_per_platform(comments):
        """每个平台各留最新一条弹幕(tag 形如 comment|抖音/comment|B站)。

        重要: 抖音弹幕量极大, 若所有平台共用"只留最新一条", 视频号/B站的弹幕
        会被抖音洪水顶掉、永远读不到。按平台分组后各留一条即可公平朗读。
        """
        seen = {}
        for d in comments:
            seen[d[1]] = d          # 后到的覆盖先到的 → 每平台只留最新
        return list(seen.values())  # 按平台首次出现顺序返回

    # ---- 取件: 事件不丢, 弹幕每平台只留最新 ----
    def pick_high(self):
        """从高优先级队列里挑下一条要读的 (text, tag), 没有则 None。

        规则(latest_only 打开时):
          · 礼物/关注/舰长/醒目留言/进场等事件: 按到达顺序读, 一条都不丢;
          · 弹幕(comment|平台): **每个平台各保留最新一条**, 积压的旧弹幕丢弃。
        这样"人一多"不会追播一堆过时弹幕, 队列不爆, 各平台也都能轮到。
        """
        drained = []
        while True:
            try:
                drained.append(self.q.get_nowait())
            except queue.Empty:
                break
        if not drained:
            return None
        if len(drained) == 1 or not self.latest_only:
            pick = drained[0]
            requeue = drained[1:]
        else:
            events = [d for d in drained if not str(d[1]).startswith("comment")]
            comments = [d for d in drained if str(d[1]).startswith("comment")]
            keepped = self._newest_per_platform(comments)
            if events:
                pick = events[0]                      # 事件优先, 保序
                requeue = events[1:] + keepped
            elif keepped:
                pick = keepped[0]                     # 全是弹幕 → 各平台轮流
                requeue = keepped[1:]
            else:
                pick = drained[-1]
                requeue = []
        for item in requeue:
            try:
                self.q.put_nowait(item)
            except queue.Full:
                break
        # 丢弃掉的那些也要销账(队列的 unfinished 计数保持平衡; 本工程没人 join, 只是不留脏数据)
        for _ in range(max(0, len(drained) - len(requeue) - 1)):
            try:
                self.q.task_done()
            except Exception:
                break
        return pick

    def _wait_gap(self):
        """离上次朗读满 gap 秒才返回(等待期间新来的弹幕会积压成"最新一条")。"""
        interval = max(self.min_interval, self.gap)
        while not self.stop.is_set():
            left = interval - (time.time() - self._last_play_at)
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    # ---- 引擎选择 ----
    def _resolve_engine(self) -> str:
        if self.provider == "sapi":
            return "sapi"
        if self.provider == "bridge":
            return "bridge"
        with self._lock:
            if self._bridge_ok:
                return "bridge"
            if self._bridge_ok is False and time.time() < self._bridge_retry_at:
                return "sapi"
        return "bridge"

    def _mark_bridge(self, ok: bool):
        with self._lock:
            self._bridge_ok = ok
            self._bridge_retry_at = time.time() + 30.0 if not ok else 0.0

    def _probe_bridge(self) -> bool:
        try:
            with urllib.request.urlopen(self.bridge_base + "/api/health", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    # ---- 合成 ----
    def _bridge_synth(self, text: str) -> bytes:
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self.bridge_base + "/api/tts", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if r.status != 200:
                raise RuntimeError(f"bridge HTTP {r.status}")
            wav = r.read()
        if not wav.startswith(b"RIFF"):
            raise RuntimeError("bridge 返回的不是 WAV")
        return wav

    def _sapi_synth(self, text: str) -> bytes | None:
        LOG_DIR.mkdir(exist_ok=True)
        out = LOG_DIR / f"sapi_{int(time.time() * 1000)}.wav"
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(SAPI_PS1), "-Out", str(out)]
        if self.sapi_voice:
            args += ["-Voice", self.sapi_voice]
        env = dict(__import__("os").environ)
        env["DANMAKU_TEXT"] = text
        try:
            subprocess.run(args, env=env, timeout=self.timeout,
                           creationflags=CREATE_NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None
        try:
            return out.read_bytes()
        except Exception:
            return None

    def _synth(self, text: str) -> tuple[str, bytes | None]:
        engine = self._resolve_engine()
        if engine == "bridge":
            try:
                return "bridge", self._bridge_synth(text)
            except Exception:
                self._mark_bridge(False)
                if self.provider == "bridge":
                    return "bridge", None
                engine = "sapi"
        return "sapi", self._sapi_synth(text)

    # ---- 线程 ----
    def run(self):
        if self.provider in ("auto", "bridge"):
            ok = self._probe_bridge()
            self._mark_bridge(ok)
            from logutil import plain
            from logutil import GREEN, RED
            if ok:
                plain(f"TTS: 已连接语音桥接 {self.bridge_base} (与 DSH 插件共用)", GREEN)
            else:
                plain(f"TTS: 语音桥接不在线 ({self.bridge_base}), 回退系统 SAPI", RED)
        while not self.stop.is_set():
            # ⓪ VIP(打赏过的观众留言): 插队 + 不等朗读间隔(只保证与上一条至少 min_interval)
            vip_mode = False
            low_mode = False
            try:
                picked = self.vip_q.get_nowait()
                vip_mode = True
            except queue.Empty:
                picked = None
            if picked is None:
                # ① 先等够"朗读间隔"(上次朗读后 gap 秒), 等待期间来的弹幕会积压
                self._wait_gap()
                if self.stop.is_set():
                    break
                # ② 再挑一条: 此刻积压的弹幕只读最新那条(事件按序不丢)
                picked = self.pick_high()
            if picked is None:
                # 高优先级为空时才考虑低优先级(欢迎/点赞), 且限制最小间隔
                now = time.time()
                if now - self._last_low_play < self.low_interval:
                    time.sleep(0.2)
                    continue
                try:
                    picked = self.low_q.get_nowait()
                except queue.Empty:
                    time.sleep(0.2)
                    continue
                low_mode = True
                self._last_low_play = time.time()
            text, _tag = picked
            wait = self.min_interval - (time.time() - self._last_play_at)
            if wait > 0:
                time.sleep(wait)
            self.current = text
            engine, wav = self._synth(text)
            if wav and self.volume < 1.0:
                wav = self._scale_wav(wav, self.volume)
            if wav:
                try:
                    winsound.PlaySound(wav, winsound.SND_MEMORY)
                except Exception:
                    pass
            self.current = ""
            self._last_play_at = time.time()
            if vip_mode:
                self.vip_q.task_done()
            elif low_mode:
                self.low_q.task_done()
            else:
                self.q.task_done()

    def _scale_wav(self, data: bytes, factor: float) -> bytes:
        """16bit PCM WAV 音量缩放(纯标准库, audioop 在 3.13+ 已移除)。"""
        try:
            w = wave.open(io.BytesIO(data), "rb")
            params = w.getparams()
            if params.sampwidth != 2:  # 只处理 16bit PCM, 其余原样
                w.close()
                return data
            frames = w.readframes(params.nframes)
            w.close()
            arr = array.array("h")
            arr.frombytes(frames)
            if sys.byteorder == "big":
                arr.byteswap()
            out = array.array("h", (max(-32768, min(32767, int(s * factor))) for s in arr))
            if sys.byteorder == "big":
                out.byteswap()
            buf = io.BytesIO()
            wo = wave.open(buf, "wb")
            wo.setnchannels(params.nchannels)
            wo.setsampwidth(2)
            wo.setframerate(params.framerate)
            wo.writeframes(out.tobytes())
            wo.close()
            return buf.getvalue()
        except Exception as e:  # noqa: BLE001
            # 不要静默回退: 原来缩放失败会毫无痕迹地按原音量播放, 看起来就像"音量没生效"。
            try:
                from logutil import YELLOW, plain
                plain(f"[warn] 音量缩放失败, 本条按原音量播放: {type(e).__name__}: {e}", YELLOW)
            except Exception:
                pass
            return data

    _last_play_at = 0.0
