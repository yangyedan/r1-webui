# r1-webui — 斐讯 R1 智能音箱 Web 控制台 & 自建 AI 后端

[English](README.en.md) | **简体中文**

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

把「斐讯 R1 智能音箱」变成**完全由自己掌控**的 AI 音箱：语音对话、天气、本地音乐、有声书、广播电台，
全部在你自己的服务器/NAS 上运行，不依赖任何第三方中转服务。附带一个功能完整的 Web 控制台。

> 零第三方依赖，只用 Python 标准库；一条命令启动。

![控制台截图](docs/screenshot-control.png)

---

## 它解决什么问题

网上流行的 R1 改造方案（如 `r1-py` + 改造版 APK）有一个隐藏问题：

**作者托管的前端会把「AI 后端地址」硬编码成他自己的服务器**，你在网页上怎么改，AI 请求最终还是走他的中转。

本项目做三件事：

1. **自建 AI 后端** —— 复刻云知声 NLU 协议，把大模型、天气、媒体能力全部跑在你自己机器上
2. **绕过作者的网页配置** —— 直接用设备本地的 WebSocket 协议改配置（[`r1_ctl.py`](r1_ctl.py)）
3. **Web 控制台** —— 状态、播放控制、曲库/故事/广播、天气、AI 配置，一个页面全包

---

## 功能

### 语音（对音箱说）
| 说法 | 行为 |
|---|---|
| 小讯小讯，讲个笑话 | AI 对话 |
| 小讯小讯，今天天气怎么样 | 天气（默认地点可配置，支持周边城市别名） |
| 小讯小讯，播放周杰伦的歌 | 播放本地曲库（自动组 100 首队列连续播放） |
| 小讯小讯，播放亚洲经典台 | 播放广播电台（支持模糊匹配） |

### Web 控制台
- **控制台** — 实时状态、播放/暂停/上下曲、音量、进度、最近播放
- **音乐库** — 全部音乐（分页/搜索）、★ 喜欢、播放历史
- **故事** — 有声书/故事长音频（2 小时+ 无限制），DLNA 点播
- **广播** — 电台列表、一键播放、自行添加/删除电台
- **AI 配置** — 改后端地址、模型、Key、系统提示词（作者网页做不到的事）
- **设置** — 天气地点（城市/经纬度）、内置音乐源、故事源

### 播放通道
- **语音点歌** → 设备内置播放器（走 AI 返回的 `SETTING_EXEC` 播放列表）
- **网页点播** → DLNA 投放（`AVTransport`，支持直播流、长音频、任意进度拖动）

---

## 架构

```
                   ┌──────────────────────────┐
   语音 ──ASR──►    │  斐讯 R1 (Android 5.1)    │
                   │  改造版 APK + DLNA 渲染器  │
                   └───────┬──────────▲───────┘
         POST /r1/ai/chat/…│          │ HTTP GET /media/...（流式/Range）
                           ▼          │
                   ┌──────────────────┴───────┐
                   │  本项目（你的 NAS/服务器） │
                   │  ┌────────────────────┐  │
                   │  │ 云知声 NLU 信封适配 │  │
                   │  ├────────────────────┤  │
                   │  │ 工具: 聊天/天气/    │  │
                   │  │      音乐/广播      │  │
                   │  ├────────────────────┤  │
                   │  │ 曲库索引 + 媒体流   │  │
                   │  ├────────────────────┤  │
                   │  │ Web UI + REST API   │  │
                   │  └────────────────────┘  │
                   └──────────┬───────────────┘
                              │ OpenAI 兼容协议
                              ▼
                    大模型（DeepSeek / Qwen / 本地 vLLM …）
```

---

## 快速开始

### 0. 前置条件

- 一台**常开**的机器：NAS（飞牛/群晖等）、软路由、树莓派、VPS 都行
- **Python 3.10+**（Debian 12 / 飞牛 fnOS 自带 3.11）
- 一个大模型 API Key（任何 OpenAI 兼容服务）
- **斐讯 R1**，固件 `3448`，已安装改造版 APK（见 §3）

### 1. 部署后端

```bash
# 下载/克隆后进入目录
cd r1-webui

# 配置
cp config.example.json config.json
vi config.json          # 至少填 llm.api_key，以及 media.public_base

# 启动
python3 server.py
```

看到这样的输出就成功了：

```
 R1 自建 AI 后端已启动
   监听      : http://0.0.0.0:8866
   AI 接口   : http://<本机IP>:8866/r1/ai/chat/completions
   曲库目录  : /path/to/music
```

自检：

```bash
curl http://127.0.0.1:8866/health
# {"ok": true, ...}
```

### 2. 装成开机自启服务（Linux）

```bash
sudo cp deploy/r1-webui.service /etc/systemd/system/
sudo vi /etc/systemd/system/r1-webui.service   # 改 User / WorkingDirectory
sudo systemctl daemon-reload
sudo systemctl enable --now r1-webui
sudo systemctl status r1-webui
```

### 3. R1 音箱端

1. **确认固件是 3448**：`adb shell getprop ro.build.version.incremental` 应输出 `3448`
2. **安装改造版 APK**（作者公开的 `r1-dummy` 仓库里有 `adb.exe`、`apps/`、`一键安装.bat`）
   —— 本仓库不包含 APK，请自行获取并确认来源可信
3. 装完后浏览器打开 `http://<音箱IP>:18888`，会 301 跳转到作者的网页控制台
   —— **不要用那个网页存 AI 配置**（会把后端地址写死回作者的服务器）
4. 用本项目的 `r1_ctl.py` 把后端地址指向你自己的机器：

```bash
python3 r1_ctl.py --ip <音箱IP>:18888 set-backend http://<你的服务器IP>:8866
python3 r1_ctl.py --ip <音箱IP>:18888 query        # 确认配置
```

### 4. 打开 Web 控制台

```
http://<你的服务器IP>:8866/?ip=<音箱IP>:18888
```

| 页面 | 地址 |
|---|---|
| 控制台 | `/?ip=<音箱IP>:18888` |
| 音乐库 | `/lib?ip=…` |
| 故事 | `/story?ip=…` |
| 广播 | `/radio?ip=…` |
| AI 配置 | `/ai?ip=…` |
| 设置 | `/cfg?ip=…` |

然后对音箱说：**小讯小讯，讲个笑话**。

---

## 配置说明

```jsonc
{
  "listen": { "host": "0.0.0.0", "port": 8866 },

  "llm": {
    "base_url": "https://api.deepseek.com/v1",  // 任何 OpenAI 兼容接口
    "api_key": "sk-...",
    "model": "deepseek-chat",                     // 需要支持 function calling
    "temperature": 0.0
  },

  "system_prompt": "你叫小讯，是这台斐讯 R1 智能音箱的助手……",

  "media": {
    "root": "/path/to/music",                     // 主目录（URL 不带前缀）
    "roots": [                                    // 可选：多目录
      { "key": "",      "path": "/path/to/music" },
      { "key": "story", "path": "/path/to/music/audiobooks" },
      { "key": "lx",    "path": "/path/to/other" }
    ],
    "public_base": "http://192.168.1.10:8866",    // 音箱能访问到的本机地址！
    "max_results": 100,                           // 播放队列长度
    "rescan_seconds": 60                          // 新增文件的索引延迟
  },

  "weather": {
    "default_location": "北京",
    "lat": 39.9042, "lon": 116.4074,              // 建议直接填坐标（县级地名常查不到）
    "timezone": "Asia/Shanghai",
    "aliases": { "北京": [39.9042, 116.4074, "北京"] }
  },

  "radio": {
    "stations": [
      { "name": "中国之声", "url": "http://example.com/live/1/64k.mp3" }
    ]
  },

  "devices": [
    { "name": "书桌", "ip": "192.168.1.50" }
  ]
}
```

**关键点**：`media.public_base` 必须是**音箱能访问到的地址**（局域网 IP），不能用 `127.0.0.1`。

---

## 支持的文件格式

`mp3` `flac` `m4a` `aac` `wav` `ogg` `opus` `wma`

- 子目录会递归扫描
- 以 `.` 或 `._` 开头的文件会被跳过（macOS 元数据垃圾）
- 新增文件最多 `rescan_seconds` 秒后自动出现，**无需重启**

---

## 命令行工具 r1_ctl.py

```bash
python3 r1_ctl.py --ip <ip>:18888 status          # 设备状态
python3 r1_ctl.py --ip <ip>:18888 query           # 读 AI 配置
python3 r1_ctl.py --ip <ip>:18888 set-backend URL # 改 AI 后端地址
python3 r1_ctl.py --ip <ip>:18888 rollback        # 回退到作者默认后端
python3 r1_ctl.py --ip <ip>:18888 music           # 读音乐源配置
python3 r1_ctl.py --ip <ip>:18888 music-provider wy
python3 r1_ctl.py --ip <ip>:18888 raw '{"action":"ai","data":{"type":"query"}}'
```

---

## 设备协议

这个项目的核心价值之一是**把 R1 改造版 APK 的协议完整摸清了**，包括几个会导致「没反应」的坑
（比如 `SETTING_EXEC` 缺 `audioUrl` 会触发设备端 `NullPointerException`）。

详见 **[docs/PROTOCOL.md](docs/PROTOCOL.md)**。

---

## 常见问题

见 **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**，包括：

- 说完话没反应
- 只能放一首就停、还提示装「酷我」
- 网页点播没声音
- 天气查不到、城市答错
- 长音频播放中断

---

## 致谢

这个项目站在不少人肩膀上：

- **[ring1012/r1-py](https://github.com/ring1012/r1-py)** — 「用小讯玩转 AI」项目的 AI 边缘服务（MIT）。
  本项目最初的协议认知来自它的实现，包括云知声信封的字段结构和 `x-r1-ai` 头的用法。
- **[ring1012/r1-dummy](https://github.com/ring1012/r1-dummy)** — 公开提供的 `adb.exe` 与改造版 APK 分发包。
  没有它就没有「不拆机」这条路。
- **恩山无线论坛 / R1 玩家社区** — 硬件资料、3448 固件、DLNA 行为、各种踩坑记录。
- **[Open-Meteo](https://open-meteo.com/)** — 免费免 Key 的天气与地理编码 API。
- **[awesome-dsh-plugin](https://awesome-dsh-plugin.com/)** — DSH 插件生态目录。
- 以及所有把斐讯 R1 从「智障音箱」救回来的人。

> 本项目与上述项目无隶属关系，也不包含它们的闭源代码/二进制。协议结论均为独立复现与真机验证。

## 免责声明

- 本项目**不包含**任何闭源 APK、固件或第三方媒体服务，只提供协议实现与自建后端。
- 音箱端的改造版 APK 由第三方提供，闭源且可能带远程 OTA，请自行评估风险。
- 本项目与斐讯、云知声、菲讯官方均无关联。
- 音乐/广播等内容请确保你有合法使用权。

## License

[MIT](LICENSE)
