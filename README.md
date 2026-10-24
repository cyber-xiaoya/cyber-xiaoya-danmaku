# 赛博小雅 · 弹幕采集器（抖音 / B站 / YouTube / 视频号）

> **本项目永久免费开源。** 唯一官方来源：https://github.com/cyber-xiaoya/cyber-xiaoya-danmaku
> 配套的数字人主仓库：[赛博小雅直播版](https://github.com/cyber-xiaoya/cyber-xiaoya-live)
> 任何付费售卖均未经授权，欢迎举报（[Issue](https://github.com/cyber-xiaoya/cyber-xiaoya-danmaku/issues)）。

> 👤 **作者：天工开帧** · [抖音](https://v.douyin.com/BgCJXnllitM/) · [哔哩哔哩](https://space.bilibili.com/11946287) · [YouTube](https://www.youtube.com/@jintaoyi) · [博客](https://www.19980303.xyz/)

把四个平台的弹幕和礼物，清洗后统一转发给你自己的数字人桥（默认 `http://127.0.0.1:8795/api/danmaku`）。

**它不是朗读器也不是数字人**——它只负责"把观众的声音搬进来"。发声和出画面由
[赛博小雅直播版](https://github.com/cyber-xiaoya/cyber-xiaoya-live) 负责。

---

## 1. 数据流

```
抖音   ── douyinLive(:1089, 第三方采集服务) ──┐
B站    ── 官方弹幕 WS(免 key, cookie 可选)   ──┤
YouTube── InnerTube /live_chat(免 key)      ──┼─▶ reader.py ──▶ POST /api/danmaku ──▶ 数字人桥(:8795)
视频号  ── CDP 调试浏览器(扫码登录)           ──┘                    │
                                                        （可选）自己念：SAPI / 语音桥
```

`reader.py` 负责：昵称缩写、敏感词丢弃、去重、连击礼物合并、VIP 插队、节流。

## 2. 快速开始

```powershell
# 1) Python 3.12（本项目除标准库外只依赖 requests / websockets）
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # 没有这个文件就 pip install requests websockets

# 2) 配置
copy config.example.json config.json
#   douyin.room_id / bilibili.room_id / youtube.* / wechat.enabled

# 3) 起
.venv\Scripts\python.exe reader.py
# 或双击 启动-弹幕朗读全链路.cmd（会连带起托盘/服务）
```

**只打印不朗读**（推荐先这样验证链路通不通）：

```powershell
.venv\Scripts\python.exe reader.py --print-only
```

## 3. 各平台怎么接

### 3.1 抖音（需要额外一个采集服务）

抖音弹幕不能直接从网页读（要签名），社区通行做法是先跑本地采集服务：

1. 开源项目 <https://github.com/jwwsjlm/douyinLive>（Go 写的，自带 ws + http，默认 1089 端口）
2. 改它目录下的 `config.yaml`（端口、抖音 cookie），启动
3. 本仓库 `config.json` 填 `douyin.room_id`（房间号），保持 `douyin.ws_base = "ws://127.0.0.1:1089"`

> ⚠️ 本仓库**不附带** `douyinLive.exe`（体积大、上游版权、且无签名）。请自行从上游项目获取。
> 不想装它？可以用主仓库的 [`tools/douyin_relay.py`](https://github.com/cyber-xiaoya/cyber-xiaoya-live/blob/main/tools/douyin_relay.py) 直接转发。

### 3.2 B站（免 key，cookie 可选）

1. 填 `bilibili.room_id`
2. 想要"登录态"（能看粉丝牌/部分事件）：双击 `配置B站Cookie.cmd`，按提示从浏览器 F12 复制 Cookie 整串贴进去
3. 不填 cookie 也能收弹幕（游客模式），只是事件少一些

> cookie 里含 `SESSDATA`，**等于你的账号登录凭证**，别发给任何人、别提交进 Git。

### 3.3 YouTube（免 API key）

- 填 `youtube.video_id`（11 位 id，或任意观看/Studio 链接）：锁定这一场
- 或填 `youtube.channel`（`@handle` / `UC...` / 频道链接）：**自动找当前正在直播的那一场**，换场次不用改配置
- 走网页的 InnerTube 接口（`/live_chat` + `get_live_chat` 长轮询），不吃官方 API 配额

### 3.4 视频号（需要浏览器登录态）

双击 `启动调试浏览器.cmd` → 打开视频号助手并进入直播间 → 本程序通过 CDP（默认 9223 端口）读聊天面板。

## 4. 和数字人桥对接

`config.json → tts`：

| 字段 | 作用 |
|---|---|
| `output` | `dh` = 转发给数字人桥；`sapi` = 本机 Windows 朗读；`bridge` = 老语音桥(8765) |
| `dh_url` | 数字人桥地址，默认 `http://127.0.0.1:8795` |
| `dh_min_interval_ms` | 两条弹幕之间最小间隔 |

转发的事件格式（桥的 `/api/danmaku`）：

```json
{"nick":"观众名","content":"弹幕内容","platform":"抖音","text":"念出来的文本"}
```

礼物额外带 `kind:"gift"`、`gift_item`、`gift_num`、`gift_value`。

`reader.py` 会自己判断哪些事件该念、哪些只通知（看 `config.json → read` 里的开关）。

## 5. 配置速查

| 段 | 关键字段 |
|---|---|
| `douyin` | `enabled` / `room_id` / `ws_base` / `auto_start` |
| `bilibili` | `enabled` / `room_id` / `cookie` / `retry_sec` |
| `youtube` | `enabled` / `video_id` / `channel` / `discover_sec` |
| `wechat` | `enabled` / `cdp_port` |
| `tts` | `output` / `dh_url` / `volume` |
| `read` | 念什么（comments/gifts/joins/follows/likes/guards）、模板、黑名单、VIP 门槛 |

## 6. 安全提示

- `config.json` 与 `bin/config.yaml` 里可能有 **Cookie（账号凭证）**，两者都已被 `.gitignore` 排除
- `logs/` 是本机日志，也已被排除
- 本仓库不含任何作者的 key / cookie / 房间号

## 7. 许可证

- 代码：[GNU AGPL-3.0](./LICENSE)
- 名称与标识、第三方采集工具：见 [NOTICE](./NOTICE)

## 8. 关于倒卖

本项目**代码免费、永久免费**。看到有人卖，请把链接发到 [Issue](https://github.com/cyber-xiaoya/cyber-xiaoya-danmaku/issues)。

> ⚠️ **本项目没有官方 QQ 群、微信群，也不卖课、不卖安装服务。**
> 任何以「官方答疑群」「内部版」「授权版」「代部署」名义收费的，都不是我，请直接拉黑。

## 9. 关注作者

赛博小雅是我（**天工开帧**）业余时间做的开源项目。想看她直播、催更、或者聊实现细节：

| 平台 | 链接 |
|---|---|
| 🎵 抖音 | <https://v.douyin.com/BgCJXnllitM/> |
| 📺 哔哩哔哩 | <https://space.bilibili.com/11946287> |
| ▶️ YouTube | <https://www.youtube.com/@jintaoyi> |
| ✖️ X / Twitter | <https://x.com/yijt1339303> |
| 📝 博客 · 教程 | <https://www.19980303.xyz/> |
| 💻 GitHub | <https://github.com/beiyege-01> |

> 问题请优先提到 [Issue](https://github.com/cyber-xiaoya/cyber-xiaoya-danmaku/issues)（公开可查）；想直接看效果：抖音直播间搜「天工开帧」。
