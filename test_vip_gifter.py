#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""test_vip_gifter.py — 验证「打赏过的观众留言优先朗读」逻辑。"""

from reader import Reader


class FakeWorker:
    def __init__(self):
        self.calls = []

    def submit(self, text, tag="", priority="high"):
        self.calls.append((text, tag, priority))
        return True


CFG = {
    "read": {
        "comments": True, "gifts": True, "joins": False, "follows": False, "likes": False,
        "vip_gifters": True, "vip_gift_ttl_minutes": 60,
        "dedupe_seconds": 10, "announce_nick": False,
        "gift_combo_wait_ms": 1800, "max_chars": 50,
        "templates": {"comment": "{nick}说{content}", "gift": "感谢{nick}的{item}"},
    },
    "tts": {}, "debug": False,
}


def main():
    w = FakeWorker()
    r = Reader(CFG, w)

    print("① 普通观众留言 →")
    r.on_event({"platform": "抖音", "type": "Chat", "data": {"name": "路人甲", "content": "你好"}})
    print("   ", w.calls[-1])

    print("② 某观众送礼物 →")
    r.on_event({"platform": "抖音", "type": "Gift",
                "data": {"name": "小明", "item": "小心心", "num": 1, "repeat_end": 1}})
    print("    已登记为打赏观众:", "小明" in r._gifters)

    print("③ 该观众留言 →")
    r.on_event({"platform": "抖音", "type": "Chat", "data": {"name": "小明", "content": "看看我"}})
    print("   ", w.calls[-1])

    print("④ 关闭开关后同一人留言 →")
    r.read_cfg["vip_gifters"] = False
    r.on_event({"platform": "抖音", "type": "Chat", "data": {"name": "小明", "content": "关掉开关后"}})
    print("   ", w.calls[-1])

    print("⑤ TTL 过期后 →")
    r.read_cfg["vip_gifters"] = True
    r._gifters["小明"] = 1.0          # 伪造很久以前送的礼
    r.on_event({"platform": "抖音", "type": "Chat", "data": {"name": "小明", "content": "很久以后"}})
    print("   ", w.calls[-1])

    ok = (w.calls[-1][2] == "high" and "vip" not in w.calls[-1][1])
    print("\n结论: TTL 过期后自动降级为普通弹幕 →", "✓ 通过" if ok else "✗ 异常")


if __name__ == "__main__":
    main()
