"""写入礼物感谢语模板池并预览实际念出来的话。

用法: .venv\\Scripts\\python.exe tools\\set_gift_templates.py        （写入 + 预览）
      .venv\\Scripts\\python.exe tools\\set_gift_templates.py --dry   （只预览, 不写文件）

分档(用户 2026-10-09 拍板):
  · 小额 & 单个   → templates.gift      （温和版 14 套）
  · 大额 或 连击  → templates.gift_big  （夸张版 5 套）
    大额阈值 = read.gift_big_min_diamond 钻; 连击 = 数量 ≥ 2 (read.gift_big_on_combo)
变量: {nick} 昵称(已缩写, 无"水友"前缀) / {gift} 数量+个+礼物名 / {item} 礼物名 / {count} 旧写法
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CONFIG = ROOT / "config.json"

SMALL = [
    "诶嘿，{nick}的{gift}，我收进小口袋啦，谢谢～",
    "呜哇！{nick}送我{gift}，这是给我的吗？",
    "好耶！{nick}的{gift}，我最喜欢啦",
    "{nick}的{gift}，嘿嘿，我要藏起来不给你看",
    "谢谢{nick}的{gift}，你怎么这么好呀",
    "唔…{nick}又送我{gift}，那我就不客气啦",
    "{nick}的{gift}到啦！我眼睛都亮了",
    "哇！{nick}的{gift}，我要抱一下再谢谢你",
    "诶呀，{nick}的{gift}，谢谢你，比心心",
    "谢谢{nick}的{gift}，我记住你啦，明天还来哦",
    "嘿嘿～{nick}的{gift}，我今天运气好好",
    "谢谢你{nick}，你的{gift}我收好啦",
    "{nick}的{gift}，我要摆在最亮的地方",
    "谢谢{nick}的{gift}，你对我最好了",
]

BIG = [
    "{nick}一出手就是{gift}，我都傻掉啦，谢谢你",
    "呜～{nick}的{gift}，谢谢你，我心跳好快",
    "喂，{nick}的{gift}，你这样会把我宠坏的",
    "哼，{nick}的{gift}，那我就收下啦（其实超开心）",
    "{nick}的{gift}…我才没有很开心呢（才怪）",
]

GUARD = [
    "{nick}给我开了{item}！呜哇，我要更努力播给你看",
    "谢谢{nick}的{item}，诶嘿，我记住你啦",
    "{nick}的{item}到手！我要开心一整天",
]

SCENES = [
    ("小额单个 · 阿糖不甜 送 小心心 x1 (1钻)", ("阿糖不甜", "小心心", 1, 1)),
    ("连击 · 快乐百晓生 送 小心心 x5 (5钻)", ("快乐百晓生", "小心心", 5, 5)),
    ("大额 · 魔渡v众生 送 玫瑰 x1 (99钻)", ("魔渡v众生", "玫瑰", 1, 99)),
    ("纯符号 ID · 🦀 送 小心心 x1", ("🦀", "小心心", 1, 1)),
    ("数字 ID · 9527123 送 小心心 x1", ("9527123", "小心心", 1, 1)),
    ("英文 ID · Tenzin Trinley 送 玫瑰 x2", ("Tenzin Trinley", "玫瑰", 2, 20)),
]


def main() -> int:
    dry = "--dry" in sys.argv
    if not dry:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        pathlib.Path(str(CONFIG) + ".bak-tpl").write_text(
            CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        tpl = cfg.setdefault("read", {}).setdefault("templates", {})
        tpl["gift"] = SMALL
        tpl["gift_big"] = BIG
        tpl["guard"] = GUARD
        cfg["read"]["gift_big_min_diamond"] = 50
        cfg["read"]["gift_big_on_combo"] = True
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {CONFIG.name}: 小额 {len(SMALL)} 套 · 大额 {len(BIG)} 套 · 舰长 {len(GUARD)} 套"
              f" · 阈值 50 钻 / 连击≥2")
    else:
        print("(--dry 只预览, 未写文件)")

    from reader import load_config, Reader
    r = Reader(load_config(CONFIG), None)
    out = []
    r._speak = lambda text, tag, priority="high": out.append(text)
    print("\n念出来会是这样:")
    for label, args in SCENES:
        out.clear()
        r._voice_gift(*args)
        print(f"  {label}\n     → {out[0] if out else '(没播报)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
