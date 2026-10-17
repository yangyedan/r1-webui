# 排错指南

按现象查。每条都是真机踩过的坑。

---

## 说了「小讯小讯，xxx」完全没反应

先看服务端日志有没有请求：

```bash
journalctl -u r1-webui -n 50        # 或直接看 python server.py 的输出
```

**有 `[http] POST /r1/ai/chat/completions`** → 请求到了，问题在响应。
**什么都没有** → 设备根本没走 AI，可能：

1. 音箱的后端地址没改成你的（用 `r1_ctl.py query` 确认）
2. 问的内容被设备本地 NLU 直接答了（笑话、时间、天气这类内置能力不一定走 AI）
3. 设备连不到你的服务器（`media.public_base` 填错 / 防火墙挡端口）
4. 用 `adb logcat | grep -E "YZSAsrEngine|NLUDispatcher"` 看设备侧有没有识别活动

---

## 有请求、但设备不吭声（或直接宕机式沉默）

九成是 **`SETTING_EXEC` 信封缺字段**导致设备端 `NullPointerException`。

设备日志里会出现：

```
at com.unisound.vui.handler.session.weather.DefaultWeatherHandler.acceptInboundEvent0
at com.unisound.vui.handler.filter.NLUDispatcher.onASRResultNet()
```

对照 [PROTOCOL.md §3](PROTOCOL.md) 把字段补全，**重点检查 `audioUrl`、`service`**。

---

## 只能放一首，放完提示「请安装酷我 App」

播放队列太短。设备放完队列会去找内置音源（作者的媒体服务 / 网易云），失败就提示装 App。

解决：把 `media.max_results` 调大（本项目默认 100）。这样点一首歌会自动组 100 首队列。

---

## 网页点播没有声音

网页点播走 **DLNA**，和语音点歌是两条通道：

1. 确认设备在线：控制台的绿点
2. 看 DLNA 是否可达：`/api/dlna/play` 返回 `{"ok":true}` 才算控制成功
3. **DLNA 端口会变**，本项目有自动重发现，但设备重启后第一次可能慢 2~3 秒
4. 用「DLNA 暂停/停止」按钮控制，控制台的播放按钮管的是 App 播放器，管不到 DLNA

---

## 长音频（2 小时+）中途停止

本项目**没有任何时长限制**：

- HTTP 服务不设 timeout，流式传输，支持 Range
- 已用 7200 秒文件实测：完整传输 + DLNA `PLAYING` + 进度正常

如果实际会断，通常是：

1. **网络**：R1 的 Wi-Fi 信号弱，长连接断开
2. **格式**：2 小时无损 FLAC 可能有 1~2GB，设备解码吃力 —— 转成 MP3(128~192k) 再放
3. **设备休眠**：长时间播放后系统省电策略介入

---

## 天气城市不对 / 查不到

- 设备**不上报位置**，完全由后端 `weather.default_location` / `lat`,`lon` 决定
- 公网 IP 定位只能到运营商出口城市（常常是省会），不准
- Open-Meteo 的**中文地名库对县级支持很差**。建议直接填 `lat`/`lon`，或用 `weather.aliases` 加别名：

```jsonc
"aliases": {
  "荆州": [30.35028, 112.19028, "荆州市"]
}
```

改完**立即生效**，网页「设置」页也能改。

---

## 新增的音乐不出现

1. 确认文件在 `media.roots` 里配置的目录下（递归子目录也算）
2. 扩展名必须是 `mp3/flac/m4a/aac/wav/ogg/opus/wma`
3. 文件名不能以 `.` 或 `._` 开头
4. 最多等 `rescan_seconds`（默认 60 秒）—— 无需重启

---

## 广播电台放不了

- 流地址必须是直连的 `mp3/aac` 直播流（不是网页播放器地址）
- 很多国内电台限 IP/地区，先用 `curl -I <url>` 确认返回 `200 audio/mpeg`
- 语音点播支持模糊匹配，但台名差别太大匹配不到

---

## 设备 OTA 一直重连 `home.phicomm.com`

斐讯原厂服务器已关闭，`otaservice` 会每 10 秒重连一次，属于原厂行为，不影响使用。
想清静可以在路由器/DNS 上屏蔽 `home.phicomm.com`。

> 注意：**不要屏蔽 `asrv3.hivoice.cn`**，部分语音链路还在用。

---

## 网页打不开

```bash
curl http://127.0.0.1:8866/health     # 服务活着吗
ss -ltn | grep 8866                   # 在监听吗
```

- 服务在跑但外部打不开 → 防火墙 / 监听地址是 `127.0.0.1`（应为 `0.0.0.0`）
- 从手机/别的电脑打不开 → 确认同一网段，且 NAS 没开"仅内网访问"策略

---

## AI 回答出错

```bash
journalctl -u r1-webui -n 30 | grep llm-error
```

- `401` → `llm.api_key` 错
- `404` → `base_url` 少了 `/v1`
- 模型不支持 function calling → 会退化成直接念 content，效果差但不报错
