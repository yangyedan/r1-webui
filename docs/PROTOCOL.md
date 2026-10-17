# 斐讯 R1 改造版固件协议说明

> 本文是逆向「R1 玩转AI」改造版 APK 得到的协议总结，用于自建后端。
> 所有结论均在真机（R1，固件 3448，Android 5.1.1）上验证。

---

## 1. 总体链路

```
唤醒词 → 本地 ASR（云知声，APK 内置）→ 文本
   ↓ POST /r1/ai/chat/completions（带对话历史）
AI 后端 → 调大模型 → 返回「云知声 NLU 信封」
   ↓
设备按信封执行：念文本(TTS) 或 播放 data.result.musicinfo
```

- ASR **在设备本地**完成，后端只收到文字。
- 设备是 **Android 5.1.1 / RK3229 / 512MB**，`/system/app` 下有
  `EchoService`、`Unisound`、`IJetty`（18888 的 Web 服务）、`OtaService` 等。

---

## 2. AI 后端接口

### 请求

```http
POST /r1/ai/chat/completions
Content-Type: application/json
r1-serial: <设备序列号>

{"messages":[{"role":"user","content":"..."}, ...]}
```

- 设备会带 **10 条左右**历史消息。
- 可选头 `x-r1-ai`：base64 编码的 AI 配置 JSON（见 §3），可作为模型参数的来源。

### 响应

必须返回 **云知声 NLU 格式**，并带响应头 `r1-sname`：

| `r1-sname` | 信封 | 用途 |
|---|---|---|
| `cn.yunzhisheng.chat` | `code: "ANSWER"` | 设备直接 TTS 念 `general.text` |
| `cn.yunzhisheng.music` | `code: "SETTING_EXEC"` | 设备播放 `data.result.musicinfo` |

---

## 3. ⚠️ 最大的坑：SETTING_EXEC 的必填字段

**`SETTING_EXEC` 少任何一个字段都可能让设备抛 `NullPointerException`，整条事件链断掉，
表现就是「说了没反应」。**

真机日志：

```
java.lang.NullPointerException: Attempt to invoke virtual method
  'boolean java.lang.String.equals(java.lang.Object)' on a null object reference
    at com.unisound.vui.handler.session.weather.DefaultWeatherHandler.acceptInboundEvent0(:33)
    at com.unisound.vui.handler.filter.NLUDispatcher.onASRResultNet()
```

注意：报错在**天气处理器**里，但实际是音乐信封缺字段 → 说明设备会把 NLU 结果广播给
**所有** handler，任何一个 handler 抛异常都会中断整条链。

### 完整字段（对照作者服务器的真实返回）

```json
{
  "semantic": {"intent": {"operations": [{"operator": "ACT_PLAY"}]}},
  "code": "SETTING_EXEC",
  "matchType": "FUZZY",
  "originIntent": {"nluSlotInfos": []},
  "data": {"result": {"success": true, "count": 1, "musicinfo": [ ... ]}},
  "confidence": 0.6313702287003818,
  "modelIntentClsScore": {},
  "history": "cn.yunzhisheng.setting.mp",
  "source": "nlu",
  "uniCarRet": {"result": {}, "returnCode": 609, "message": "http post reuqest error"},
  "asr_recongize": "播放XX。",
  "rc": 0,
  "general": {"text": "好的，已为您播放XX", "type": "T"},
  "service": "cn.yunzhisheng.music",
  "audioUrl": "http://asrv3.hivoice.cn/trafficRouter/r/yxOMl6",
  "returnCode": 0,
  "retTag": "nlu",
  "nluProcessTime": "255",
  "text": "播放XX",
  "responseId": "<32位hex>"
}
```

**最容易漏的是 `audioUrl`**（`service` 和 `general.text/type` 也要有）。
`ANSWER` 信封同理，字段见 [`protocol.py`](../protocol.py)。

### 各字段来源

| 字段 | 说明 |
|---|---|
| `audioUrl` | 云知声的固定提示音资源。音乐用 `.../r/yxOMl6`，聊天用 `.../r/TRdECS` |
| `service` | 与 `r1-sname` 一致 |
| `general.text` | 设备会念这句 |
| `data.result.musicinfo` | 播放列表，见 §4 |

---

## 4. musicinfo 结构

```json
{
  "success": true,
  "count": 2,
  "musicinfo": [
    {
      "id": "228908",
      "title": "晴天",
      "artist": "周杰伦",
      "album": "叶惠美",
      "duration": "269",
      "imgUrl": "https://.../cover.jpg",
      "url": "http://your-server/media/song.mp3"
    }
  ]
}
```

- `url` 可以是**任意 http/https 地址**，设备会直接流式拉取（支持 Range，可拖进度）。
- `duration` 单位秒；填 0 也能播（设备会用音频自身时长）。
- 队列**至少给几首**，只给 1 首时播放结束设备会回落到内置音源，失败后提示「装酷我 App」。

---

## 5. 设备本地控制通道（WebSocket）

```
ws://<音箱IP>:18888/ws/status
```

### 消息格式

```json
{"action": "<名字>", "data": {...}}
```

连接后设备会持续推送状态：

```json
{"states":{"playIndex":0,"playList":[...],"playMode":2,"playState":1},
 "position":{"play_time":36546,"total_time":226828,"status":1},
 "volume":5,"serial":"XXXXXXXX"}
```

`playState`: `0` 空闲 / `1` 播放中 / `2` 已暂停 / `3` 缓冲中。

### 支持的 action

| action | data | 说明 |
|---|---|---|
| `play` | `{"index":0}` | 播放列表第 N 首 |
| `pause` | — | 暂停 |
| `next` / `prev` | — | 上/下一首 |
| `volume` | `{"volume":0-100}` | 音量 |
| `mode` | `{"mode":N}` | 播放模式 |
| `seek` | `{"position":秒}` | 跳转 |
| `play_list` | `{"index":0,"playList":[...]}` | **实测固件会忽略，不可用** |
| `search` | `{"keyword":"..."}` | 设备内置搜索，返回 `{"action":"search","status":"ok"}` |
| `ai` | `{"type":"query"}` / `save1` / `save2` | AI 配置读写 |
| `music_service` | `{"type":"query"}` / `{"type":"save","config":"..."}` | 音乐源配置 |
| `story_service` | 同上 | 故事源配置 |
| `weather` | `{"type":"query"}` | 天气配置 |
| `hass` | — | Home Assistant 相关 |

心跳（每 3 秒）：

```json
{"action":"message","data":{"what":-1,"arg1":-1,"arg2":-1,"obj":"keep alive"}}
```

### AI 配置读写（关键）

```jsonc
// 读
{"action":"ai","data":{"type":"query"}}
// 回：{"action":"ai","data":{"enabled":true,"config":"<JSON字符串>"}}

// 写「后端地址」（0x10000=65536, arg1=2, arg2=1启用/0禁用）
{"action":"ai","data":{"type":"save1","what":65536,"arg1":2,"arg2":1,
                       "obj":"http://your-server:8866"}}

// 写「模型配置」
{"action":"ai","data":{"type":"save2","what":65536,"arg1":2,"arg2":3,
                       "obj":"{\"choice\":\"OpenAi\",\"key\":\"sk-...\",\"model\":\"...\",\"endpoint\":\"https://...\",\"systemPrompt\":\"...\",\"extraBody\":\"\",\"cdn\":\"\",\"aiType\":0}"}}
```

> **作者的网页把 `save1` 的 `obj` 硬编码成他自己的服务器地址**
> （`https://r1-py.thd.dpdns.org`），所以用他的网页永远改不掉后端。
> 必须用上面的 WS 命令直接写。本项目提供 [`r1_ctl.py`](../r1_ctl.py)。

---

## 6. DLNA 投放（网页点播用）

R1 上跑着 Neptune(Cling) 的 **MediaRenderer**，可以走标准 UPnP 播放任意 URL。

1. **SSDP 发现**（UDP 组播 `239.255.255.250:1900`，ST=`urn:schemas-upnp-org:device:MediaRenderer:1`）
   → 拿到 `LOCATION`，例如 `http://<ip>:1810/`
2. **拉设备描述 XML**，找 `AVTransport` 的 `controlURL`
3. **SOAP**：`SetAVTransportURI` + `Play`

注意：

- **端口是动态的**（重启后可能变，例如 1810 → 1142），不能写死，要重新发现。
- DLNA 播放与 App 内置播放器是**两条独立通道**，WS 的 `pause/next` 管不到 DLNA，
  要用 `AVTransport.Pause/Stop`。
- 直播流（`audio/mpeg`、无 Content-Length）也能投放。

实现在 [`dlna.py`](../dlna.py)。

---

## 7. 已知固件行为

| 现象 | 原因 |
|---|---|
| 设置里改后端地址无效 | 作者网页硬编码 `save1` 的 obj |
| `play_list` 发过去没反应 | 固件忽略该 action（发数组还会报 `ClassCastException`） |
| 放完一首就停并提示装「酷我」 | 队列太短，设备回落到内置音源失败 |
| 报 NPE 后完全没反应 | `SETTING_EXEC` 缺字段（见 §3） |
| 天气总是同一个城市 | 后端配置的默认地点；设备不报位置 |
| OTA 每 10 秒重连 `home.phicomm.com` | 斐讯原厂服务器已关闭，属原厂行为，可 DNS 屏蔽 |
