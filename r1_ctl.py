#!/usr/bin/env python3
"""r1_ctl —— 斐讯 R1 控制器（纯标准库，Mac/Linux/NAS/Windows 都能跑）

R1 上的改造版 APK 在 18888 端口开了一个 WebSocket 控制通道，
消息格式：{"action": <名字>, "data": {...}}

用法：
    python3 r1_ctl.py --ip 192.168.1.50 status
    python3 r1_ctl.py --ip 192.168.1.50 query
    python3 r1_ctl.py --ip 192.168.1.50 set-backend http://192.168.1.10:8866
    python3 r1_ctl.py --ip 192.168.1.50 rollback
    python3 r1_ctl.py --ip 192.168.1.50 music                 # 看音乐源配置
    python3 r1_ctl.py --ip 192.168.1.50 music-provider wy     # kw/tx/wy
    python3 r1_ctl.py --ip 192.168.1.50 raw '{"action":"ai","data":{"type":"query"}}'
"""

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time

AUTHOR_BACKEND = "https://r1-py.thd.dpdns.org"  # 作者默认后端，用于 rollback
MUSIC_PROVIDERS = ("kw", "tx", "wy")


# ---------------------------------------------------------------- 极简 WebSocket

class WS:
    def __init__(self, host, port=18888, path="/ws/status", timeout=8):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("握手阶段连接被断开")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        status_line = header.split(b"\r\n")[0].decode("latin-1")
        if "101" not in status_line:
            raise RuntimeError("握手失败: " + status_line)
        self.buf = rest

    def send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        frame = bytearray([0x81])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            frame.append(0x80 | n)
        elif n < 65536:
            frame.append(0x80 | 126)
            frame += struct.pack(">H", n)
        else:
            frame.append(0x80 | 127)
            frame += struct.pack(">Q", n)
        frame += mask
        frame += bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(frame))

    def _try_parse(self):
        if len(self.buf) < 2:
            return None
        b1, b2 = self.buf[0], self.buf[1]
        ln = b2 & 0x7F
        idx = 2
        if ln == 126:
            if len(self.buf) < 4:
                return None
            ln = struct.unpack(">H", self.buf[2:4])[0]
            idx = 4
        elif ln == 127:
            if len(self.buf) < 10:
                return None
            ln = struct.unpack(">Q", self.buf[2:10])[0]
            idx = 10
        if len(self.buf) < idx + ln:
            return None
        payload = self.buf[idx:idx + ln]
        self.buf = self.buf[idx + ln:]
        opcode = b1 & 0x0F
        if opcode == 8:
            return ("__close__", None)
        if opcode == 1:
            return ("text", payload.decode("utf-8", "replace"))
        return ("other", None)

    def recv(self, timeout=6.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = self._try_parse()
            if got:
                kind, text = got
                if kind == "__close__":
                    return None
                if kind == "text":
                    return text
                continue
            remain = max(0.1, deadline - time.time())
            self.sock.settimeout(remain)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self.buf += chunk
        return None

    def wait_action(self, action, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.recv(max(0.2, deadline - time.time()))
            if text is None:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            if obj.get("action") == action:
                return obj
        return None

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 命令

def parse_ip(ip):
    if ":" in ip:
        host, port = ip.rsplit(":", 1)
        return host, int(port)
    return ip, 18888


def cmd_status(host, port):
    ws = WS(host, port)
    try:
        print(ws.recv(6) or "(无推送)")
    finally:
        ws.close()


def _request(host, port, action, data, want_action, timeout=8.0):
    ws = WS(host, port)
    try:
        ws.recv(2)  # 丢掉初始状态推送
        ws.send({"action": action, "data": data})
        return ws.wait_action(want_action, timeout)
    finally:
        ws.close()


def cmd_query(host, port):
    r = _request(host, port, "ai", {"type": "query"}, "ai")
    if not r:
        print("没有收到回复")
        return 1
    cfg = r.get("data", {}).get("config")
    enabled = r.get("data", {}).get("enabled")
    print("AI 已启用:", enabled)
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            pass
    print(json.dumps(cfg, ensure_ascii=False, indent=2) if isinstance(cfg, dict) else cfg)
    return 0


def cmd_set_backend(host, port, url):
    r = _request(
        host, port, "ai",
        {"type": "save1", "what": 65536, "arg1": 2, "arg2": 1, "obj": url},
        "ai",
    )
    ok = bool(r and r.get("status") == "ok")
    print(("✅ 后端已设为 " if ok else "❌ 失败 ") + url)
    return 0 if ok else 1


def cmd_music(host, port):
    r = _request(host, port, "music_service", {"type": "query"}, "music_service")
    print(json.dumps(r, ensure_ascii=False, indent=2) if r else "没有回复")
    return 0


def cmd_music_provider(host, port, provider):
    if provider not in MUSIC_PROVIDERS:
        print(f"provider 只能是 {MUSIC_PROVIDERS}")
        return 1
    cfg = json.dumps({"provider": provider, "endpoint": ""})
    r = _request(
        host, port, "music_service",
        {"type": "save", "config": cfg},
        "music_service",
    )
    print(json.dumps(r, ensure_ascii=False) if r else "(无回复)")
    return 0


def cmd_music_endpoint(host, port, provider, endpoint):
    cfg = json.dumps({"provider": provider, "endpoint": endpoint})
    r = _request(
        host, port, "music_service",
        {"type": "save", "config": cfg},
        "music_service",
    )
    print(json.dumps(r, ensure_ascii=False) if r else "(无回复)")
    return 0


def cmd_raw(host, port, payload):
    obj = json.loads(payload)
    ws = WS(host, port)
    try:
        ws.recv(2)
        ws.send(obj)
        for _ in range(6):
            text = ws.recv(4)
            if text is None:
                break
            print(text)
            if '"status"' in text or '"data"' in text:
                break
    finally:
        ws.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="斐讯 R1 控制器")
    ap.add_argument("--ip", required=True, help="音箱 IP，可带端口，如 192.168.1.50:18888")
    ap.add_argument("--port", type=int, default=18888)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("query")

    p = sub.add_parser("set-backend")
    p.add_argument("url")

    sub.add_parser("rollback")
    sub.add_parser("music")

    p = sub.add_parser("music-provider")
    p.add_argument("provider")

    p = sub.add_parser("music-endpoint")
    p.add_argument("provider")
    p.add_argument("endpoint")

    p = sub.add_parser("raw")
    p.add_argument("payload")

    a = ap.parse_args()
    host, port = parse_ip(a.ip)
    if a.port:
        port = a.port

    try:
        if a.cmd == "status":
            return cmd_status(host, port)
        if a.cmd == "query":
            return cmd_query(host, port)
        if a.cmd == "set-backend":
            return cmd_set_backend(host, port, a.url)
        if a.cmd == "rollback":
            return cmd_set_backend(host, port, AUTHOR_BACKEND)
        if a.cmd == "music":
            return cmd_music(host, port)
        if a.cmd == "music-provider":
            return cmd_music_provider(host, port, a.provider)
        if a.cmd == "music-endpoint":
            return cmd_music_endpoint(host, port, a.provider, a.endpoint)
        if a.cmd == "raw":
            return cmd_raw(host, port, a.payload)
    except (ConnectionError, OSError) as e:
        print(f"连接失败：{e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
