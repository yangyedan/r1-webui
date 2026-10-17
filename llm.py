"""极简 OpenAI 兼容客户端 —— 只用标准库，不依赖 requests / openai SDK。"""

import json
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def chat(cfg: dict, messages: list, tools: list | None = None, timeout: int = 90) -> dict:
    """调用 OpenAI 兼容的 /chat/completions，返回 choices[0].message。"""
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise LLMError("llm.base_url 未配置")
    url = base + "/chat/completions"

    body = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "temperature": cfg.get("temperature", 0.0),
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = cfg.get("tool_choice", "auto")
    # 允许透传厂商私有参数（frequency_penalty / thinking 等）
    extra = cfg.get("extra_body") or {}
    if isinstance(extra, dict):
        body.update(extra)

    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (cfg.get("api_key") or ""),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"LLM HTTP {e.code}: {detail}")
    except Exception as e:  # 网络/超时
        raise LLMError(f"LLM 请求失败: {type(e).__name__}: {e}")

    try:
        data = json.loads(raw)
        return data["choices"][0]["message"]
    except Exception as e:
        raise LLMError(f"LLM 响应无法解析: {e}; raw={raw[:300]}")
