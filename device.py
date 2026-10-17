"""设备桥接：把 R1 的 WebSocket 控制通道包装成同步函数，给 HTTP 层用。"""

import json
import time

from r1_ctl import WS

DEFAULT_PORT = 18888


def parse_target(ip, default_port=DEFAULT_PORT):
    ip = (ip or "").strip()
    if not ip:
        raise ValueError("缺少 ip 参数")
    if ":" in ip:
        host, port = ip.rsplit(":", 1)
        return host, int(port)
    return ip, default_port


def status(ip, timeout=4.0):
    host, port = parse_target(ip)
    ws = WS(host, port, timeout=timeout)
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = ws.recv(max(0.2, deadline - time.time()))
            if text is None:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            if "states" in obj:
                return obj
        return None
    finally:
        ws.close()


def command(ip, action, data=None, want=None, timeout=6.0):
    """发一条命令，并尽量等到对应 action 的回应。"""
    host, port = parse_target(ip)
    ws = WS(host, port, timeout=timeout)
    try:
        ws.recv(2)  # 丢掉初始推送
        payload = {"action": action}
        if data is not None:
            payload["data"] = data
        ws.send(payload)
        want = want or action
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = ws.recv(max(0.2, deadline - time.time()))
            if text is None:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            if obj.get("action") in (want, "error"):
                return obj
        return None
    finally:
        ws.close()


# ---------------------------------------------------------------- 业务封装

def ai_query(ip):
    r = command(ip, "ai", {"type": "query"}, want="ai")
    if not r:
        return None
    data = r.get("data") or {}
    cfg = data.get("config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            pass
    return {"enabled": data.get("enabled"), "config": cfg, "raw": r}


def ai_save(ip, enabled, backend, cfg):
    """先写后端地址（save1），再写模型配置（save2）。"""
    r1 = command(
        ip, "ai",
        {"type": "save1", "what": 65536, "arg1": 2, "arg2": 1 if enabled else 0,
         "obj": backend},
        want="ai",
    )
    ok1 = bool(r1 and r1.get("status") == "ok")
    cfg_text = cfg if isinstance(cfg, str) else json.dumps(cfg, ensure_ascii=False)
    r2 = command(
        ip, "ai",
        {"type": "save2", "what": 65536, "arg1": 2, "arg2": 3, "obj": cfg_text},
        want="ai",
    )
    ok2 = bool(r2 and r2.get("status") == "ok")
    return {"backend_ok": ok1, "config_ok": ok2, "save1": r1, "save2": r2}


def music_query(ip):
    return command(ip, "music_service", {"type": "query"}, want="music_service")


def music_save(ip, provider, endpoint=""):
    cfg = json.dumps({"provider": provider, "endpoint": endpoint}, ensure_ascii=False)
    return command(ip, "music_service", {"type": "save", "config": cfg}, want="music_service")


def story_query(ip):
    return command(ip, "story_service", {"type": "query"}, want="story_service")


def story_save(ip, provider, endpoint=""):
    cfg = json.dumps({"provider": provider, "endpoint": endpoint}, ensure_ascii=False)
    return command(ip, "story_service", {"type": "save", "config": cfg}, want="story_service")


def play_list(ip, items, index=0):
    """尝试注入播放列表；不同固件接受度不同，返回结果由调用方判断。"""
    payload = {
        "index": index,
        "playList": [
            {
                "album": it.get("album") or "",
                "artist": it.get("artist") or "",
                "imgUrl": it.get("imgUrl") or "",
                "itemId": str(it.get("id") or it.get("itemId") or ""),
                "itemType": 0,
                "title": it.get("title") or "",
                "url": it.get("url") or "",
            }
            for it in items
        ],
    }
    command(ip, "play_list", payload, want="play_list")
    time.sleep(0.4)
    command(ip, "play", {"index": index}, want="play")
    time.sleep(0.8)
    return status(ip)
