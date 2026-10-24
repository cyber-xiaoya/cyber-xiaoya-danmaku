"""模板机制测试: 单串兼容 / 多套随机不连续重复 / 礼物名回退 / 昵称缩写。

跑法: E:\\deepseek-works\\vidu-dh-s2-component\\.venv\\Scripts\\python.exe tools\\test_templates.py
（在 multi-danmaku-tts 目录下跑也行, 不依赖网络与平台连接）
"""
import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reader import DEFAULT_CONFIG, Reader, abbr_nick  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def mk(templates=None, abbr=True):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["tts"]["output"] = "dh"
    cfg["read"]["abbr_nick"] = abbr
    if templates is not None:
        cfg["read"]["templates"] = templates
    return Reader(cfg, None)


def main():
    print("① 昵称规则 v2（不加「水友」前缀；混排按用户拍板的 v1: 保留紧跟汉字的数字/字母）")
    cases = [
        ("阿糖不甜", "阿糖", "纯中文 → 前 2 字"),
        ("魔渡v众生", "魔渡", "中英混排 → 从第一个汉字起取 2 个可读字符"),
        ("一2三", "一2", "中数混排 → 保留数字(用户拍板 v1)"),
        ("小2明", "小2", "中数混排 → 保留数字"),
        ("ᰔᩚƵHΛПG偉୧⍤⃝🍃", "偉", "中文+符号混排 → 只读到汉字(符号跳过)"),
        ("9527123", "9527", "纯数字 → 前 4 位"),
        ("Tenzin Trinley", "Tenzin", "英文 → 第一个单词"),
        ("Mi", "Mi", "英文单词"),
        ("🦀", "", "纯符号 → 返回空(由泛称兜底)"),
        ("βζηικλ", "", "希腊字母 → 返回空"),
    ]
    for src, want, why in cases:
        got = abbr_nick(src)
        check(f"{why}: {src!r} → {got!r}", got == want, "" if got == want else f"应为 {want!r}")

    print("\n② 泛称兜底(纯符号 ID)")
    r0 = mk({"comment": "{nick}说{content}"})
    got = r0._speak_nick("🦀")
    check("纯符号 → 从 nick_fallback 随机取泛称", got in ("小可爱", "宝子"), got)
    r0b = mk({"comment": "{nick}说{content}"})
    r0b.read_cfg["nick_fallback"] = []
    check("泛称列表为空 → 返回空串", r0b._speak_nick("🦀") == "")

    print("\n③ 老配置兼容: 单个字符串模板照旧")
    r = mk({"gift": "恰喽，感谢{nick}的投喂"})
    check("字符串模板 + 昵称缩写",
          r._tpl("gift", {"nick": r._speak_nick("阿糖不甜"), "item": "小心心", "count": ""},
                 "x") == "恰喽，感谢阿糖的投喂")

    print("\n② 多套随机: 每次换一句, 且不与上一次重复")
    sets = ["A{nick}{item}{count}", "B{nick}{item}{count}", "C{nick}{item}{count}",
            "D{nick}{item}{count}"]
    r2 = mk({"gift": sets})
    seen = [r2._tpl("gift", {"nick": "水友甲", "item": "玫瑰", "count": ""}, "x") for _ in range(30)]
    check("30 次都在四套里取值", all(s[0] in "ABCD" for s in seen))
    check("每套都被用到过", len({s[0] for s in seen}) == 4, f"用到 {sorted({s[0] for s in seen})}")
    check("没有连续重复", all(seen[i][0] != seen[i + 1][0] for i in range(len(seen) - 1)))

    print("\n③ 礼物短语: 数量+个+礼物名（不要「x3」）")
    r3 = mk({"gift": "诶嘿，{nick}的{gift}，我收下啦"})
    check("单个 → 只念礼物名",
          r3._tpl("gift", {"nick": "阿糖", "gift": r3._gift_phrase("小心心", 1)}, "x")
          == "诶嘿，阿糖的小心心，我收下啦")
    check("3 个 → 3个小心心",
          r3._tpl("gift", {"nick": "阿糖", "gift": r3._gift_phrase("小心心", 3)}, "x")
          == "诶嘿，阿糖的3个小心心，我收下啦")
    check("超多 → 一大堆小心心", r3._gift_phrase("小心心", 520) == "一大堆小心心")
    r3b = mk({"gift": "感谢{nick}的{item}{count}"})
    check("旧写法 {item}{count} 仍兼容",
          r3b._tpl("gift", {"nick": "阿糖", "item": "玫瑰", "count": " x3"}, "x")
          == "感谢阿糖的玫瑰 x3")

    print("\n④ 礼物名回退 + 昵称缩写开关")
    r4 = mk({"gift": "谢谢{nick}的{gift}"})
    check("礼物名为空 → 回退成「礼物」",
          r4._tpl("gift", {"nick": "乙", "gift": r4._gift_phrase("" or "礼物", 1)}, "x")
          == "谢谢乙的礼物")
    r4b = mk({"gift": "谢谢{nick}的{gift}"}, abbr=False)
    check("abbr_nick 关掉 → 念全名",
          r4b._tpl("gift", {"nick": r4b._speak_nick("阿糖不甜"), "gift": "玫瑰"}, "x")
          == "谢谢阿糖不甜的玫瑰")

    print("\n⑤ 其他场景共用同一机制(guard / superchat)")
    r5 = mk({"guard": ["{nick}开舰长啦！{item}到手", "感谢{nick}的{item}"],
             "superchat": "谢谢{nick}的醒目留言：{content}"})
    g1 = r5._tpl("guard", {"nick": "丙", "item": "舰长"}, "x")
    g2 = r5._tpl("guard", {"nick": "丙", "item": "舰长"}, "x")
    check("guard 多套可用且不重复", g1 != g2, f"{g1} / {g2}")
    check("superchat 变量替换",
          r5._tpl("superchat", {"nick": "丁", "content": "加油"}, "x")
          == "谢谢丁的醒目留言：加油")

    print("\n⑥ 按礼物份量分档: 小额走 gift / 大额或连击走 gift_big")
    cfg_sets = {"gift": ["小A{nick}{gift}", "小B{nick}{gift}"],
                "gift_big": ["大A{nick}{gift}", "大B{nick}{gift}"]}
    r6 = mk(cfg_sets)
    # 直接调 _tpl 模拟分档池选择
    small = [r6._tpl("gift", {"nick": "甲", "gift": "小心心"}, "x",
                     pool=cfg_sets["gift"]) for _ in range(8)]
    big = [r6._tpl("gift", {"nick": "甲", "gift": "小心心"}, "x",
                   pool=cfg_sets["gift_big"]) for _ in range(8)]
    check("小额池只出小额话术", all(s[0] == "小" for s in small))
    check("大额池只出夸张话术", all(s[0] == "大" for s in big))
    # 端到端: 调 _voice_gift 看它选了哪个池(用 _speak 捕获)
    spoken = []
    r6._speak = lambda text, tag, priority="high": spoken.append(text)
    r6._voice_gift("甲", "小心心", 1, 1)          # 小额单个 → 小池
    r6._voice_gift("乙", "小心心", 3, 1)          # 连击 → 大池
    r6._voice_gift("丙", "玫瑰", 1, 520)          # 大额(520钻) → 大池
    check("小额单个 → 小池", spoken[0].startswith("小"), spoken[0])
    check("连击 3 个 → 大池", spoken[1].startswith("大"), spoken[1])
    check("大额 520 钻 → 大池", spoken[2].startswith("大"), spoken[2])
    r6b = mk({"gift": ["唯一{nick}{gift}"]})       # 没配 gift_big → 回落
    sp2 = []
    r6b._speak = lambda text, tag, priority="high": sp2.append(text)
    r6b._voice_gift("丁", "玫瑰", 5, 999)
    check("未配 gift_big → 回落用 gift 池", sp2 and sp2[0].startswith("唯一"), sp2[0] if sp2 else "")

    print("\n⑦ 同一观众不重复(即使全局也只两套)")
    r7 = mk({"gift": ["甲{nick}{gift}", "乙{nick}{gift}"]})
    seq = [r7._tpl("gift", {"nick": "阿糖", "gift": "玫瑰"}, "x", who="阿糖") for _ in range(20)]
    pairs_same = [i for i in range(len(seq) - 1) if seq[i] == seq[i + 1]]
    check("同一观众连续两次不重复", not pairs_same, f"重复位置 {pairs_same}")

    print("\n⑧ 关闭「念弹幕」时: 仍转发 + 带 answer_only 标记 + 提示词标签字段")
    def chat_case(comments_on, content="今天唱什么", nick="阿糖不甜", platform="抖音"):
        r8 = mk({"comment": "{nick}说{content}"})
        r8.read_cfg["comments"] = comments_on
        got = []
        r8._speak = lambda text, tag, priority="high", extra=None: got.append(
            {"text": text, "tag": tag, "priority": priority, "extra": extra or {}})
        r8._ev_chat(platform, {"name": nick, "content": content})
        return got[0] if got else None

    on = chat_case(True)
    off = chat_case(False)
    check("开启时: 正常念(不带 answer_only)", on and not on["extra"].get("answer_only"), str(on))
    check("关闭时: 仍然转发(不再丢弃)", off is not None, str(off))
    check("关闭时: answer_only=True", off and off["extra"].get("answer_only") is True)
    check("关闭时: 带缩写后的称呼(阿糖, 不带全名)", off and off["extra"].get("nick") == "阿糖",
          str(off["extra"].get("nick")) if off else "")
    check("关闭时: 带原文与平台", off and off["extra"].get("content") == "今天唱什么"
          and off["extra"].get("platform") == "抖音")
    check("关闭时: 敏感词仍然丢弃(不会让 LLM 复述)",
          chat_case(False, content="主播倒立一个") is None)

    print(f"\n结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
