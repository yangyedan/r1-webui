"""LLM 可调用的工具：普通对话 / 播放本地音乐 / 查询天气。

每个工具返回 (response_body, response_headers) —— 已经是要发给 R1 的完整信封。
把工具集写小一点是刻意的：模型只会调用这里声明过的东西，
声明太多反而容易乱调。
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

import protocol

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".wma"}

# ---------------------------------------------------------------- 工具声明


def tool_specs() -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": "chat",
                "description": (
                    "回答用户的普通问题、闲聊、知识问答、讲故事、解释概念。"
                    "凡是不能用音乐或天气工具处理的，都用这个。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {
                            "type": "string",
                            "description": "要对用户说的话，口语化、简短，适合音箱朗读，不带 Markdown",
                        }
                    },
                    "required": ["answer"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "playMusic",
                "description": (
                    "播放音乐、歌曲、儿歌、歌手歌曲、歌单、排行榜。"
                    "用户想听歌/音乐时用这个。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "搜索关键词，如歌曲名或歌单名"},
                        "author": {"type": "string", "description": "歌手名，可为空"},
                        "song_name": {"type": "string", "description": "歌名，可为空"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "queryWeather",
                "description": "查询某地天气，或询问今天/明天/后天/未来几天的天气。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "城市名，如 北京、杭州；不确定就留空"},
                        "offset_day": {
                            "type": "integer",
                            "description": "0=今天 1=明天 2=后天 3=大后天，默认 0",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "playRadio",
                "description": "播放广播电台 / FM / 直播电台 / 收音机。用户想听广播、电台时用这个。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "电台名称关键词，如 亚洲经典台、粤语、音乐台、热歌；不确定就留空",
                        }
                    },
                    "required": [],
                },
            },
        },
    ]


# ---------------------------------------------------------------- 媒体库

_media_cache = {"at": 0.0, "items": []}


def root_specs(cfg: dict) -> list:
    """把配置解析成 [{key, path}]。

    两种写法都支持：
      media.root  : "/path/to/music"              单目录（主目录，URL 不带前缀）
      media.roots : [{"key":"",    "path":"..."},  多目录；主目录 key 留空
                     {"key":"lx",  "path":"..."}]  其它目录 URL 前缀 @key/
    """
    m = cfg.get("media") or {}
    roots = m.get("roots")
    out = []
    if isinstance(roots, list) and roots:
        for i, r in enumerate(roots):
            if isinstance(r, str):
                out.append({"key": "" if i == 0 else "r%d" % i, "path": r})
            elif isinstance(r, dict):
                out.append(
                    {
                        "key": r.get("key") or ("" if i == 0 else "r%d" % i),
                        "path": r.get("path") or "",
                    }
                )
    if not out and m.get("root"):
        out = [{"key": "", "path": m["root"]}]
    if not out:
        out = [{"key": "", "path": "./media"}]
    return [s for s in out if s.get("path")]


def root_for_key(cfg: dict, key: str):
    for s in root_specs(cfg):
        if s["key"] == key:
            return s["path"]
    return None


def _scan_media(cfg: dict) -> list:
    now = time.time()
    if now - _media_cache["at"] < int(cfg.get("media", {}).get("rescan_seconds", 60)):
        return _media_cache["items"]

    items = []
    specs = root_specs(cfg)
    all_paths = [(s["key"], os.path.abspath(s["path"])) for s in specs]
    for spec in specs:
        root = os.path.abspath(spec["path"])
        key = spec["key"]
        if not os.path.isdir(root):
            continue
        # 如果别的根目录嵌在当前根里面（比如 story 在 音乐/audiobooks），
        # 就在这里剪掉，避免同一批文件被两份扫描、曲库里出现重复
        nested = {
            p
            for k, p in all_paths
            if k != key and p != root and (p + os.sep).startswith(root + os.sep)
        }
        for dirpath, dirs, files in os.walk(root):
            # 跳过隐藏目录（含 macOS 的 ._* 元数据目录）和嵌套的其它根目录
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and os.path.abspath(os.path.join(dirpath, d)) not in nested
            ]
            for fn in sorted(files):
                if fn.startswith("."):  # ._xxx.mp3 / .DS_Store 这类垃圾，不是真音频
                    continue
                stem, ext = os.path.splitext(fn)
                if ext.lower() not in AUDIO_EXT:
                    continue
                rel_fs = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                rel = ("@%s/%s" % (key, rel_fs)) if key else rel_fs
                parent = "" if os.path.abspath(dirpath) == root else os.path.basename(dirpath)
                items.append(
                    {
                        "rel": rel,
                        "fs": os.path.join(dirpath, fn),
                        "root": root,
                        "root_key": key,
                        "stem": stem,
                        "parent": parent,
                        "haystack": (stem + " " + rel_fs).lower(),
                    }
                )
    _media_cache["at"] = now
    _media_cache["items"] = items
    return items


def _song_item(cfg: dict, it: dict) -> dict:
    url = (cfg.get("media", {}).get("public_base") or "").rstrip("/") + "/media/" + urllib.parse.quote(it["rel"])
    # 名字里常见的 "歌手 - 歌名" 或中文圈更常见的 "歌名-歌手" 拆一下
    artist, title = "", it["stem"]
    m = re.match(r"^(.+?)\s+[-–—_]\s+(.+)$", it["stem"])
    if m:
        title, artist = m.group(1), m.group(2)
    else:
        m = re.match(r"^(.+?)[-–—_](.+)$", it["stem"])
        if m:
            title, artist = m.group(1), m.group(2)
    # 字段名照抄作者音乐服务的真实返回（已实测），别乱改：
    # {"id","title","artist","album","duration","imgUrl","url"}
    return {
        "id": hashlib.sha1(it["rel"].encode("utf-8")).hexdigest()[:16],
        "title": title,
        "artist": artist,
        "album": it.get("parent") or "",
        "duration": int(it.get("duration") or 0),
        "imgUrl": "",
        "url": url,
    }


def search_media(cfg: dict, keyword: str) -> dict:
    items = _scan_media(cfg)
    limit = max(1, int(cfg.get("media", {}).get("max_results", 10)))
    tokens = [t for t in re.split(r"[\s,，。]+", (keyword or "").lower()) if t]

    hits = []
    if tokens:
        scored = []
        for it in items:
            score = sum(1 for t in tokens if t in it["haystack"])
            if score == len(tokens):
                scored.append((score, it))
        scored.sort(key=lambda x: (-x[0], len(x[1]["rel"])))
        hits = [it for _s, it in scored][:limit]

    # 关键：队列要够长。
    # 只返回 1 首时，设备放完就回落到它自己的音源，失败后会提示「装酷我 App」。
    # 所以命中之后补满：同专辑/同歌手目录 → 同歌手 → 实在不够就用同根目录的其它歌。
    if hits:
        used = {it["rel"] for it in hits}
        head = hits[0]

        def push(it):
            if len(hits) >= limit:
                return False
            if it["rel"] in used:
                return False
            hits.append(it)
            used.add(it["rel"])
            return True

        if head.get("parent"):
            for it in items:
                if len(hits) >= limit:
                    break
                if it.get("parent") == head["parent"]:
                    push(it)

        if len(hits) < limit:
            _t, art = _split_title(head["stem"])
            art = (art or "").strip().lower()
            if art:
                for it in items:
                    if len(hits) >= limit:
                        break
                    _t2, a2 = _split_title(it["stem"])
                    if (a2 or "").strip().lower() == art:
                        push(it)

        if len(hits) < limit:
            for it in items:
                if len(hits) >= limit:
                    break
                if it.get("root") == head.get("root"):
                    push(it)

    if not hits and items:
        hits = items[:limit]
    return {
        "success": True,
        "count": len(hits),
        "musicinfo": [_song_item(cfg, it) for it in hits],
    }


# ---------------------------------------------------------------- 天气

_WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}


def _http_json(url: str, timeout: int = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _norm_place(s: str) -> str:
    """去掉行政区划后缀，便于「公安」和「公安县」判定为同一地。"""
    s = (s or "").strip()
    for suf in ("特别行政区", "自治区", "自治县", "自治州", "省", "市", "县", "区", "镇"):
        if len(s) > len(suf) and s.endswith(suf):
            return s[: -len(suf)]
    return s


def query_weather(cfg: dict, location: str, offset_day: int) -> str:
    w = cfg.get("weather", {})
    default_loc = (w.get("default_location") or "").strip()
    location = (location or default_loc or "北京").strip()

    # 只有「用户没指定城市 / 指定的就是默认城市」时才用本地坐标，
    # 这样问「北京天气」不会被强行答成本地天气。
    # 注意用 _norm_place 归一化，避免「公安」被解析成广西的同名地。
    cfg_lat, cfg_lon = w.get("lat"), w.get("lon")
    use_local = bool(cfg_lat and cfg_lon) and (
        not (location or "").strip()
        or location == default_loc
        or _norm_place(location) == _norm_place(default_loc)
    )

    if use_local:
        lat, lon, place = cfg_lat, cfg_lon, default_loc or location
    else:
        # ① 先查别名表（config.weather.aliases），适合 Open-Meteo 查不到的小地方
        aliases = w.get("aliases") or {}
        alias = aliases.get(location) or aliases.get(_norm_place(location))
        if isinstance(alias, (list, tuple)) and len(alias) >= 2:
            lat, lon = alias[0], alias[1]
            place = alias[2] if len(alias) > 2 else location
        else:
            # ② 再自动试「原名 / 原名+市 / 原名+县」，中文地名库对县级支持很差
            lat = lon = None
            candidates = [location, location + "市", location + "县", _norm_place(location) + "市"]
            seen = set()
            for cand in candidates:
                if not cand or cand in seen:
                    continue
                seen.add(cand)
                q = urllib.parse.urlencode(
                    {"name": cand, "count": 1, "language": "zh", "format": "json"}
                )
                try:
                    geo = _http_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
                except Exception as e:
                    return f"查询{location}的位置失败了：{e}"
                hit = (geo.get("results") or [None])[0]
                if hit:
                    lat, lon = hit["latitude"], hit["longitude"]
                    place = hit.get("name") or location
                    break
            if lat is None:
                return f"没查到{location}的天气，换个说法试试。"

    d = max(0, min(int(offset_day or 0), 6))
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": w.get("timezone") or "Asia/Shanghai",
        "forecast_days": d + 1,
    }
    try:
        data = _http_json(
            "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
        )
    except Exception as e:
        return f"查询{place}天气失败了：{e}"

    if d == 0 and data.get("current"):
        cur = data["current"]
        daily = (data.get("daily") or {}).get("weather_code") or []
        desc = _WMO.get(cur.get("weather_code"), "未知")
        tmax = tmin = None
        if (data.get("daily") or {}).get("temperature_2m_max"):
            tmax = data["daily"]["temperature_2m_max"][0]
            tmin = data["daily"]["temperature_2m_min"][0]
        parts = [f"{place}现在{desc}，气温{round(cur.get('temperature_2m', 0))}摄氏度"]
        if tmax is not None and tmin is not None:
            parts.append(f"今天{round(tmin)}到{round(tmax)}度")
        parts.append(f"湿度{round(cur.get('relative_humidity_2m', 0))}%")
        return "，".join(parts) + "。"

    daily = data.get("daily") or {}
    codes = daily.get("weather_code") or []
    if d < len(codes):
        desc = _WMO.get(codes[d], "未知")
        tmax = daily["temperature_2m_max"][d]
        tmin = daily["temperature_2m_min"][d]
        label = {0: "今天", 1: "明天", 2: "后天"}.get(d, f"{d}天后")
        return f"{place}{label}{desc}，气温{round(tmin)}到{round(tmax)}摄氏度。"
    return f"暂时查不到{place}那天的天气。"


# ---------------------------------------------------------------- 广播


def radio_stations(cfg: dict) -> list:
    r = cfg.get("radio") or {}
    out = []
    for s in r.get("stations") or []:
        if isinstance(s, dict) and s.get("name") and s.get("url"):
            out.append({"name": str(s["name"]).strip(), "url": str(s["url"]).strip()})
    return out


def find_station(cfg: dict, name: str):
    """按名字模糊匹配电台；匹配不到返回 None。"""
    stations = radio_stations(cfg)
    if not stations:
        return None
    key = (name or "").strip().lower()
    if not key:
        return stations[0]
    for s in stations:
        n = s["name"].lower()
        if key in n or n in key:
            return s
    toks = [t for t in re.split(r"[\s,，。]+", key) if t]
    for s in stations:
        n = s["name"].lower()
        if all(t in n for t in toks):
            return s
    # 逐字匹配：命中字最多的那个
    best, best_score = None, 0
    for s in stations:
        score = sum(1 for ch in key if ch in s["name"].lower())
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 2 else None


def radio_payload(cfg: dict, station: dict) -> dict:
    """把电台包成设备认识的 musicinfo；目标台排第一，其余排队方便换台。"""
    stations = radio_stations(cfg)
    ordered = [station] + [s for s in stations if s["url"] != station["url"]][:9]
    info = []
    for s in ordered:
        info.append(
            {
                "id": "radio-" + hashlib.sha1(s["url"].encode("utf-8")).hexdigest()[:12],
                "title": s["name"],
                "artist": "直播",
                "album": "广播",
                "duration": 0,
                "imgUrl": "",
                "url": s["url"],
            }
        )
    return {"success": True, "count": len(info), "musicinfo": info}


# ---------------------------------------------------------------- 调度


def dispatch(name: str, args: dict, cfg: dict, serial: str = "") -> tuple[dict, dict]:
    """执行某个工具，返回 (响应体, 响应头)。"""
    args = args or {}

    if name == "chat":
        text = (args.get("answer") or "").strip() or "我在呢。"
        return protocol.answer(text), protocol.headers_for(protocol.SNAME_CHAT)

    if name == "playMusic":
        kw = " ".join(
            str(args.get(k) or "") for k in ("author", "song_name", "keyword")
        ).strip()
        kw = kw or "音乐"
        result = search_media(cfg, kw)
        display = " ".join(
            str(args.get(k) or "") for k in ("author", "song_name")
        ).strip() or kw
        if result["count"] == 0:
            text = "本地曲库里没有找到这首歌，先换一首或者把文件放进 media 目录吧。"
            return protocol.answer(text), protocol.headers_for(protocol.SNAME_CHAT)
        body = protocol.execution(result, display)
        return body, protocol.headers_for(protocol.SNAME_MUSIC)

    if name == "playRadio":
        station = find_station(cfg, args.get("name") or "")
        if not station:
            return (
                protocol.answer("还没有配置广播电台，去控制台的「广播」页加一个吧。"),
                protocol.headers_for(protocol.SNAME_CHAT),
            )
        body = protocol.execution(radio_payload(cfg, station), station["name"])
        return body, protocol.headers_for(protocol.SNAME_MUSIC)

    if name == "queryWeather":
        text = query_weather(cfg, args.get("location") or "", int(args.get("offset_day") or 0))
        return protocol.answer(text), protocol.headers_for(protocol.SNAME_CHAT)

    return (
        protocol.answer(f"工具 {name} 还没实现。"),
        protocol.headers_for(protocol.SNAME_CHAT),
    )
