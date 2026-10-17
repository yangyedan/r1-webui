"""仅用于本地自测：假 OpenAI 兼容服务，按关键词返回不同的 tool_call。

触发词（ASCII，方便从 PowerShell 里发）：
    MUSIC    -> playMusic
    WEATHER  -> queryWeather
    其它      -> chat
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        body = json.loads(raw.decode("utf-8", "replace") or "{}")
        msgs = body.get("messages") or []
        user = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                user = m.get("content") or ""
                break

        if "MUSIC" in user or "音乐" in user or "歌" in user:
            name, args = "playMusic", {"keyword": "jay"}
        elif "WEATHER" in user or "天气" in user:
            name, args = "queryWeather", {"location": "北京", "offset_day": 0}
        else:
            name, args = "chat", {"answer": "我在呢，刚被你叫醒。"}

        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }
            ],
        }

        out = json.dumps({"choices": [{"message": msg}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    a = ap.parse_args()
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
