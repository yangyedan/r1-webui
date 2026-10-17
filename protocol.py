"""云知声(Yunzhisheng) NLU 响应信封构造器。

R1 上被改造过的小讯 APK，期望后端返回**云知声 NLU 格式**的 JSON，
并通过响应头 r1-sname 告知它该用哪个渲染/播报通道。

这两种信封的字段是从 r1-py 的 entry.py / tools.py 反推出来的，
是本项目与设备之间唯一必须严格遵守的契约。

  ANSWER       -> 普通对话，设备直接 TTS 播报 general.text
  SETTING_EXEC -> 结构化指令，设备据此播放媒体/执行动作
"""

import uuid

# 设备用来判断"按聊天还是按播放处理"的响应头
HEADER_SNAME = "r1-sname"
SNAME_CHAT = "cn.yunzhisheng.chat"
SNAME_MUSIC = "cn.yunzhisheng.music"

# 云知声用于小讯本体的固定音效资源；照着原实现保留即可
DEFAULT_AUDIO_URL = "http://asrv3.hivoice.cn/trafficRouter/r/TRdECS"
MUSIC_AUDIO_URL = "http://asrv3.hivoice.cn/trafficRouter/r/yxOMl6"


def _rid() -> str:
    return uuid.uuid4().hex


def answer(text: str) -> dict:
    """普通对话回复。设备会把 general.text 念出来。"""
    return {
        "code": "ANSWER",
        "matchType": "NOT_UNDERSTAND",
        "confidence": 0.8,
        "history": "cn.yunzhisheng.chat",
        "source": "nlu",
        "asr_recongize": "OK",
        "rc": 0,
        "general": {
            "style": "CQA_common_customized",
            "text": text,
            "type": "T",
            "resourceId": "904757",
        },
        "returnCode": 0,
        "audioUrl": DEFAULT_AUDIO_URL,
        "retTag": "nlu",
        "service": SNAME_CHAT,
        "nluProcessTime": "717",
        "text": "OK",
        "responseId": _rid(),
    }


def execution(data: dict, display_text: str, service: str = SNAME_MUSIC) -> dict:
    """结构化执行回复（播放音乐/音频等），data 原样放进 data.result。"""
    return {
        "semantic": {"intent": {"operations": [{"operator": "ACT_PLAY"}]}},
        "code": "SETTING_EXEC",
        "matchType": "FUZZY",
        "originIntent": {"nluSlotInfos": []},
        "data": {"result": data},
        "confidence": 0.6313702287003818,
        "modelIntentClsScore": {},
        "history": "cn.yunzhisheng.setting.mp",
        "source": "nlu",
        "uniCarRet": {
            "result": {},
            "returnCode": 609,
            "message": "http post reuqest error",
        },
        "asr_recongize": f"播放{display_text}。",
        "rc": 0,
        # 下面这几个字段必须和作者服务器返回的一模一样：
        # 设备的 DefaultWeatherHandler 会拿 audioUrl 等字段做 .equals()，
        # 缺任何一个都会 NPE，整条事件链断掉，音乐处理器收不到事件。
        "general": {"text": f"好的，已为您播放{display_text}", "type": "T"},
        "service": service,
        "audioUrl": MUSIC_AUDIO_URL,
        "returnCode": 0,
        "retTag": "nlu",
        "nluProcessTime": "255",
        "text": f"播放{display_text}",
        "responseId": _rid(),
    }


def headers_for(service: str, extra: dict | None = None) -> dict:
    """设备靠 r1-sname 响应头选择通道；缺了它会表现成"听不懂"。"""
    h = {"r1-sname": service}
    if extra:
        for k, v in extra.items():
            h[k] = v
    return h
