"""R1 自建 AI 后端 —— 零依赖，纯标准库。

跑起来：
    python server.py            # 读同目录 config.json
    python server.py --port 9000

对设备暴露的接口：
    POST /r1/ai/chat/completions   AI 对话（云知声信封返回）
    GET  /api/music/search?keyword= 本地曲库搜索
    GET  /media/<文件>              音频文件（支持 Range）
    GET  /health                    健康检查
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Windows 控制台默认是 GBK，中文日志会乱码；统一切到 UTF-8
if os.name == "nt":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import device  # noqa: E402
import dlna  # noqa: E402
import llm  # noqa: E402
import tools  # noqa: E402

CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")
WEBUI_PATH = os.path.join(HERE, "webui.html")
STATE_PATH = os.path.join(HERE, "webui_state.json")
HISTORY_PATH = os.path.join(HERE, "history.json")
FAV_PATH = os.path.join(HERE, "favorites.json")
HISTORY_MAX = 300


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data) -> None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        traceback.print_exc()


def _split_title(stem: str):
    import re as _re

    m = _re.match(r"^(.+?)\s+[-–—_]\s+(.+)$", stem)
    if m:
        return m.group(1), m.group(2)
    m = _re.match(r"^(.+?)[-–—_](.+)$", stem)
    if m:
        return m.group(1), m.group(2)
    return stem, ""


def media_item(cfg: dict, rel: str) -> dict:
    """把曲库相对路径变成一个前端可用的曲目对象。"""
    import hashlib

    rel = (rel or "").lstrip("/")
    base = ((cfg.get("media") or {}).get("public_base") or "").rstrip("/")
    stem = os.path.splitext(os.path.basename(rel))[0]
    title, artist = _split_title(stem)
    parent = os.path.dirname(rel).split("/")[-1] if "/" in rel else ""
    return {
        "rel": rel,
        "id": hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16],
        "title": title,
        "artist": artist,
        "album": parent,
        "url": base + "/media/" + urllib.parse.quote(rel),
    }


def record_history(cfg: dict, rel: str) -> None:
    items = _read_json(HISTORY_PATH, [])
    items = [x for x in items if x.get("rel") != rel]
    it = media_item(cfg, rel)
    it["at"] = int(time.time())
    items.insert(0, it)
    _write_json(HISTORY_PATH, items[:HISTORY_MAX])


def _load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> bool:
    """把内存里的配置写回 config.json（网页改设置时用）。"""
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return True
    except Exception:
        traceback.print_exc()
        return False


def _save_state(d: dict) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def current_backend(cfg: dict) -> str:
    """网页上显示的「AI 后端地址」：优先上次写入的，其次本机地址。"""
    st = _load_state()
    if st.get("backend"):
        return st["backend"]
    return (cfg.get("media") or {}).get("public_base") or ""


# ---------------------------------------------------------------- 配置


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        if os.path.exists(EXAMPLE_PATH):
            shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
        print(f"[!] 没有找到 config.json，已从 config.example.json 生成：{CONFIG_PATH}")
        print("    请填入 llm.api_key 后重新启动。")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not (cfg.get("llm") or {}).get("api_key"):
        # 不退出：服务照常起来，/health 可用，方便先部署好再填 key
        print("[!] config.json 里 llm.api_key 是空的，服务会启动但对话会失败。")
        print("    填好 key 后 systemctl restart r1-selfhost 即可。")
    return cfg


# ---------------------------------------------------------------- 对话主流程


def build_messages(cfg: dict, messages: list) -> list:
    """补系统提示 + 时间 + "必须调用工具"的硬规则（沿用原实现的做法）。"""
    messages = list(messages or [])
    if any(m.get("role") == "system" for m in messages):
        return messages

    t = time.gmtime(time.time() + 8 * 3600)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    time_ctx = "当前时间：%s（%s）（UTC+8）" % (
        time.strftime("%Y年%m月%d日 %H:%M", t),
        weekdays[t.tm_wday],
    )
    prompt = cfg.get("system_prompt") or ""
    rule = (
        "\n【重要规则】你必须通过调用一个 function tool 来回复用户，不要直接输出文本。"
        "普通聊天/问答用 chat 工具，听歌用 playMusic，听广播/电台用 playRadio，天气用 queryWeather。"
        "回答要口语化、简短，适合音箱朗读，不要用 Markdown。"
    )
    system = "\n".join(x for x in (time_ctx, prompt) if x) + rule
    messages.insert(0, {"role": "system", "content": system})
    return messages


def device_llm_override(cfg: dict, headers) -> dict:
    """设备可能带 x-r1-ai（base64 的 JSON）覆盖模型配置，优先用它。"""
    raw = headers.get("x-r1-ai")
    if not raw:
        return cfg["llm"]
    try:
        cfg2 = json.loads(base64.b64decode(raw).decode("utf-8"))
        merged = dict(cfg["llm"])
        for k in ("model", "endpoint", "base_url", "key", "api_key", "temperature"):
            if cfg2.get(k):
                merged["base_url" if k == "endpoint" else "api_key" if k == "key" else k] = cfg2[k]
        return merged
    except Exception:
        return cfg["llm"]


def handle_chat(cfg: dict, body: dict, headers) -> tuple[dict, dict]:
    messages = body.get("messages") or []
    serial = headers.get("r1-serial") or ""
    llm_cfg = device_llm_override(cfg, headers)
    print(
        f"[chat] in serial={serial or '-'} x-r1-ai={bool(headers.get('x-r1-ai'))} "
        f"msgs={len(messages)}"
    )

    msgs = build_messages(cfg, messages)

    if not (llm_cfg.get("api_key") or "").strip():
        return _answer("后端还没配置模型 key，请先在 config.json 里填好 llm.api_key，再重启服务。")

    msg = llm.chat(llm_cfg, msgs, tools=tools.tool_specs())

    calls = msg.get("tool_calls") or []
    if not calls:
        # 模型没调工具（例如不支持 function calling）→ 退化成直接念 content
        text = (msg.get("content") or "").strip() or "我在呢。"
        return _answer(text)

    call = calls[0]
    fn = (call.get("function") or {})
    name = fn.get("name") or ""
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except Exception:
        args = {}

    print(f"[chat] serial={serial or '-'} tool={name} args={args}")
    try:
        payload, hdrs = tools.dispatch(name, args, cfg, serial)
    except Exception as e:
        traceback.print_exc()
        return _answer(f"工具执行出错：{e}")

    hdrs = dict(hdrs)
    hdrs["tool_call_id"] = name
    # 注意：HTTP 头只能是 latin-1，中文必须转义，否则响应会被客户端判为协议错误
    hdrs["tool-args"] = json.dumps(args, ensure_ascii=True)
    return payload, hdrs


def _answer(text: str) -> tuple[dict, dict]:
    import protocol

    return protocol.answer(text), protocol.headers_for(protocol.SNAME_CHAT)


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "r1-selfhost/1.0"
    protocol_version = "HTTP/1.1"
    # 音频可能放 2 小时以上，连接必须能一直挂着，不能有读/写超时
    timeout = None

    # ---- 基础工具

    def _json(self, obj, status=200, extra_headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            s = str(v)
            try:
                s.encode("latin-1")
            except UnicodeEncodeError:  # 头里混进中文就转义，别让整个响应坏掉
                s = s.encode("ascii", "backslashreplace").decode("ascii")
            self.send_header(k, s)
        self.end_headers()
        self.wfile.write(data)

    def _text(self, s, status=200):
        data = s.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_webui(self):
        if not os.path.exists(WEBUI_PATH):
            return self._text("webui.html 缺失", status=500)
        with open(WEBUI_PATH, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _ip(qs):
        ip = (qs.get("ip") or [""])[0].strip()
        if not ip:
            raise ValueError("缺少 ip 参数，例如 ?ip=192.168.1.50:18888")
        return ip

    def _story_page(self, qs):
        """故事/有声书：只列 media.roots 里 key == 'story' 的目录。"""
        cfg = self.server.cfg
        story_root = tools.root_for_key(cfg, "story") or ""
        try:
            offset = int((qs.get("offset") or ["0"])[0])
        except ValueError:
            offset = 0
        try:
            limit = min(200, int((qs.get("limit") or ["80"])[0]))
        except ValueError:
            limit = 80
        kw = (qs.get("q") or [""])[0].strip().lower()

        idx = tools._scan_media(cfg)
        rows = [it for it in idx if it.get("root_key") == "story"]
        if kw:
            rows = [it for it in rows if kw in it["haystack"]]
        total = len(rows)
        page = rows[offset: offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "root": story_root,
            "exists": bool(story_root) and os.path.isdir(story_root),
            "items": [media_item(cfg, it["rel"]) for it in page],
        }

    def log_message(self, fmt, *args):  # 安静点
        pass

    def handle_one_request(self):
        # 客户端（尤其老 Android）常直接断连接，别把 traceback 刷进日志
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # ---- GET

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = urllib.parse.parse_qs(u.query)
        print(f"[http] GET {self.path} from {self.client_address[0]}")

        try:
            if path == "/health":
                return self._json(
                    {"ok": True, "time": int(time.time()), "service": "r1-selfhost"}
                )
            # ---- 网页端（/?ip=x.x.x.x:18888、/ai、/lib、/story、/cfg）
            if path in ("/", "/index.html", "/ai", "/music", "/lib", "/story", "/radio", "/cfg"):
                return self._serve_webui()

            # ---- 曲库搜索（老路径 /api/music/search 兼容设备，新路径 /api/library 给网页）
            if path == "/api/library" or path == "/api/music/search" or path.endswith("/song-list"):
                kw = (qs.get("keyword") or qs.get("q") or [""])[0]
                return self._json(tools.search_media(self.server.cfg, kw))

            if path == "/api/devices":
                return self._json({"devices": self.server.cfg.get("devices") or []})

            if path == "/api/status":
                ip = self._ip(qs)
                st = device.status(ip)
                return self._json(st if st else {"error": "无法连接设备"})

            if path == "/api/ai":
                ip = self._ip(qs)
                resp = device.ai_query(ip)
                if not resp:
                    return self._json({"error": "读取失败"})
                resp["backend"] = current_backend(self.server.cfg)
                return self._json(resp)

            if path == "/api/music":
                ip = self._ip(qs)
                return self._json(device.music_query(ip) or {"error": "读取失败"})

            if path == "/api/history":
                return self._json({"items": _read_json(HISTORY_PATH, [])})

            if path == "/api/favorites":
                return self._json({"items": _read_json(FAV_PATH, [])})

            if path == "/api/weather":
                return self._json(self.server.cfg.get("weather") or {})

            if path == "/api/radio":
                return self._json({"stations": tools.radio_stations(self.server.cfg)})

            if path == "/api/story":
                return self._json(self._story_page(qs))

            if path == "/api/story_service":
                ip = self._ip(qs)
                return self._json(device.story_query(ip) or {"error": "读取失败"})

            if path == "/api/library/all":
                root = ((self.server.cfg.get("media") or {}).get("root") or "")
                try:
                    offset = int((qs.get("offset") or ["0"])[0])
                except ValueError:
                    offset = 0
                try:
                    limit = min(200, int((qs.get("limit") or ["60"])[0]))
                except ValueError:
                    limit = 60
                kw = (qs.get("q") or [""])[0].strip().lower()
                idx = tools._scan_media(self.server.cfg)
                rows = [it for it in idx if not kw or kw in it["haystack"]]
                total = len(rows)
                page = rows[offset: offset + limit]
                return self._json(
                    {
                        "total": total,
                        "offset": offset,
                        "limit": limit,
                        "root": root,
                        "items": [media_item(self.server.cfg, it["rel"]) for it in page],
                    }
                )

            if path.startswith("/media/"):
                return self._serve_media(path[len("/media/"):])
            return self._json({"error": "not found", "path": path}, status=404)
        except ValueError as e:
            return self._json({"error": str(e)}, status=400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, status=500)

    def do_HEAD(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/media/"):
            return self._serve_media(u.path[len("/media/"):], head=True)
        self._text("", status=200)

    # ---- POST

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = urllib.parse.parse_qs(u.query)
        print(f"[http] POST {self.path} from {self.client_address[0]}")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8", "replace") or "{}")
            except Exception:
                body = {}

            if path.endswith("/chat/completions"):
                headers = {k.lower(): v for k, v in self.headers.items()}
                payload, hdrs = handle_chat(self.server.cfg, body, headers)
                return self._json(payload, extra_headers=hdrs)

            # ---- 网页端 API
            if path == "/api/cmd":
                ip = self._ip(qs)
                r = device.command(ip, body.get("action"), body.get("data"))
                return self._json(r or {"error": "设备无回应"})

            if path == "/api/ai":
                ip = self._ip(qs)
                backend = (body.get("backend") or "").strip()
                r = device.ai_save(
                    ip, bool(body.get("enabled")), backend, body.get("config") or {}
                )
                if r.get("backend_ok"):
                    st = _load_state()
                    st["backend"] = backend
                    _save_state(st)
                return self._json(r)

            if path == "/api/music":
                ip = self._ip(qs)
                r = device.music_save(
                    ip, body.get("provider") or "wy", body.get("endpoint") or ""
                )
                return self._json(r or {"error": "设备无回应"})

            if path == "/api/story_service":
                ip = self._ip(qs)
                r = device.story_save(
                    ip, body.get("provider") or "bilibili", body.get("endpoint") or ""
                )
                return self._json(r or {"error": "设备无回应"})

            if path == "/api/weather":
                cfg = self.server.cfg
                w = cfg.setdefault("weather", {})
                if "default_location" in body:
                    w["default_location"] = (body.get("default_location") or "").strip()
                for k in ("lat", "lon"):
                    if k in body and body[k] not in (None, ""):
                        try:
                            w[k] = float(body[k])
                        except (TypeError, ValueError):
                            pass
                if body.get("timezone"):
                    w["timezone"] = body["timezone"]
                if isinstance(body.get("aliases"), dict):
                    w["aliases"] = body["aliases"]
                ok = save_config(cfg)
                return self._json({"ok": ok, "weather": w})

            if path == "/api/radio":
                cfg = self.server.cfg
                r = cfg.setdefault("radio", {})
                stations = [s for s in (r.get("stations") or []) if isinstance(s, dict)]
                op = (body.get("op") or "add").lower()
                url = (body.get("url") or "").strip()
                if op == "delete":
                    if not url:
                        return self._json({"error": "缺少 url"}, status=400)
                    r["stations"] = [s for s in stations if (s.get("url") or "").strip() != url]
                else:
                    name = (body.get("name") or "").strip()
                    if not name or not url:
                        return self._json({"error": "name 和 url 必填"}, status=400)
                    # 同一个 url 只保留一条
                    stations = [s for s in stations if (s.get("url") or "").strip() != url]
                    stations.append({"name": name, "url": url})
                    r["stations"] = stations
                ok = save_config(cfg)
                return self._json({"ok": ok, "stations": tools.radio_stations(cfg)})

            if path == "/api/play":
                ip = self._ip(qs)
                st = device.play_list(
                    ip, body.get("items") or [], int(body.get("index") or 0)
                )
                return self._json({"ok": bool(st), "status": st or {}})

            if path == "/api/favorites":
                rel = body.get("rel") or ""
                if not rel:
                    return self._json({"error": "缺少 rel"}, status=400)
                favs = _read_json(FAV_PATH, [])
                rels = {x.get("rel") for x in favs}
                if body.get("on", True):
                    if rel not in rels:
                        favs.insert(0, media_item(self.server.cfg, rel))
                else:
                    favs = [x for x in favs if x.get("rel") != rel]
                _write_json(FAV_PATH, favs)
                return self._json({"ok": True, "items": favs})

            # ---- DLNA 投放（网页点播走这条路）
            if path == "/api/dlna/play":
                host = device.parse_target(self._ip(qs))[0]
                r = dlna.cast(
                    host,
                    body.get("url") or "",
                    body.get("title") or "",
                    body.get("artist") or "",
                )
                return self._json(r)

            if path == "/api/dlna/pause":
                return self._json(dlna.transport_pause(device.parse_target(self._ip(qs))[0]))

            if path == "/api/dlna/stop":
                return self._json(dlna.transport_stop(device.parse_target(self._ip(qs))[0]))

            return self._json({"error": "not found", "path": path}, status=404)
        except llm.LLMError as e:
            print(f"[llm-error] {e}")
            payload, hdrs = _answer("后端模型暂时没能回答，稍后再试一次吧。")
            return self._json(payload, extra_headers=hdrs)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, status=500)

    # ---- 静态音频（带 Range，方便拖动进度）

    def _serve_media(self, rel_encoded: str, head: bool = False):
        cfg = self.server.cfg
        # 长音频（2 小时+）要能一直传，连接上不能有任何超时
        try:
            self.connection.settimeout(None)
        except Exception:
            pass
        rel = urllib.parse.unquote(rel_encoded).lstrip("/")

        # "@key/xxx" —— 来自 media.roots 里的附属目录
        if rel.startswith("@"):
            key, _sep, sub = rel[1:].partition("/")
            root_path = tools.root_for_key(cfg, key)
            if not root_path:
                return self._json({"error": "unknown root", "key": key}, status=404)
            root = os.path.abspath(root_path)
            sub_rel = sub
        else:
            root = os.path.abspath((cfg.get("media") or {}).get("root") or "./media")
            sub_rel = rel

        target = os.path.abspath(os.path.join(root, sub_rel))
        if not target.startswith(root + os.sep):
            return self._json({"error": "forbidden"}, status=403)
        if not os.path.isfile(target):
            return self._json({"error": "not found", "file": rel}, status=404)

        # 记录播放历史（只有真正被拉流的音频才记）
        if not head:
            try:
                if os.path.splitext(target)[1].lower() in tools.AUDIO_EXT:
                    record_history(cfg, rel)
            except Exception:
                pass

        size = os.path.getsize(target)
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = 200

        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                status = 206

        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        with open(target, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0 and self.server.running:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config()
    listen = cfg.get("listen") or {}
    host = args.host or listen.get("host") or "0.0.0.0"
    port = args.port or int(listen.get("port") or 8866)

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.cfg = cfg
    httpd.running = True

    media_root = os.path.abspath((cfg.get("media") or {}).get("root") or "./media")
    os.makedirs(media_root, exist_ok=True)

    print("=" * 58)
    print(" R1 自建 AI 后端已启动")
    print(f"   监听      : http://{host}:{port}")
    print(f"   AI 接口   : http://<本机IP>:{port}/r1/ai/chat/completions")
    print(f"   曲库目录  : {media_root}")
    print(f"   模型      : {(cfg.get('llm') or {}).get('model')}")
    print("   在 R1 的 :18888 控制器里把 AI endpoint 填成上面这个地址")
    print("=" * 58)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
