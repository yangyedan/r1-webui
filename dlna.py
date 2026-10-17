"""极简 DLNA/UPnP AVTransport 控制点（纯标准库）。

R1 上跑着 Neptune(Cling) 的 DLNA MediaRenderer，可以用标准 UPnP 命令
把任意 http 音频 URL 推给它播放 —— 这就是网页端「点播」的实现方式：

    discover()            -> 找到局域网里的 MediaRenderer
    describe(location)    -> 解析出 AVTransport 的 controlURL
    set_uri(ctrl, url)    -> SetAVTransportURI
    play(ctrl)            -> Play
"""

import re
import socket
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = [
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "ssdp:all",
]

AVT = "urn:schemas-upnp-org:service:AVTransport:1"

_SOAP_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:{action} xmlns:u="{service}">
{args}
    </u:{action}>
  </s:Body>
</s:Envelope>"""


def _ns(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def ssdp_search(timeout=3.0, targets=None):
    """返回 [{'location':..., 'st':..., 'usn':...}, ...]"""
    targets = targets or SEARCH_TARGETS
    found = {}
    for st in targets:
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 2\r\n"
            f"ST: {st}\r\n\r\n"
        ).encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        try:
            sock.sendto(msg, SSDP_ADDR)
            while True:
                try:
                    data, addr = sock.recvfrom(8192)
                except socket.timeout:
                    break
                text = data.decode("utf-8", "replace")
                headers = {}
                for line in text.split("\r\n")[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                loc = headers.get("location")
                if not loc:
                    continue
                key = (addr[0], loc)
                found[key] = {
                    "ip": addr[0],
                    "location": loc,
                    "st": headers.get("st", ""),
                    "usn": headers.get("usn", ""),
                    "server": headers.get("server", ""),
                }
        finally:
            sock.close()
    return list(found.values())


def describe(location):
    """拉取设备描述 XML，返回 {friendlyName, avtransport_control, ...}"""
    with urlopen(Request(location, headers={"User-Agent": "r1-webui/1.0"}), timeout=6) as r:
        raw = r.read().decode("utf-8", "replace")
    root = ET.fromstring(raw)

    info = {"friendlyName": "", "control": None, "serviceType": None}
    for el in root.iter():
        if _ns(el.tag) == "friendlyName" and el.text and not info["friendlyName"]:
            info["friendlyName"] = el.text.strip()

    for svc in root.iter():
        if _ns(svc.tag) != "service":
            continue
        st, ctrl = "", ""
        for child in svc:
            n = _ns(child.tag)
            if n == "serviceType":
                st = (child.text or "").strip()
            elif n == "controlURL":
                ctrl = (child.text or "").strip()
        if st.startswith("urn:schemas-upnp-org:service:AVTransport") and ctrl:
            info["control"] = urljoin(location, ctrl)
            info["serviceType"] = st
            break
    return info


def find_device(ip, timeout=3.0):
    """按 IP 找到某个 MediaRenderer 的 AVTransport 控制地址。"""
    for d in ssdp_search(timeout=timeout):
        if d["ip"] != ip:
            continue
        try:
            info = describe(d["location"])
        except Exception:
            continue
        if info.get("control"):
            info["location"] = d["location"]
            return info
    return None


def _soap(control_url, service, action, args_xml, timeout=8):
    body = _SOAP_TMPL.format(action=action, service=service, args=args_xml)
    req = Request(
        control_url,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{service}#{action}"',
            "User-Agent": "r1-webui/1.0",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _didl(url, title="", artist=""):
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="-1" restricted="1">'
        f"<dc:title>{_esc(title)}</dc:title>"
        f"<upnp:artist>{_esc(artist)}</upnp:artist>"
        "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
        f"<res protocolInfo=\"http-get:*:audio/mpeg:*\">{_esc(url)}</res>"
        "</item></DIDL-Lite>"
    )


def _esc(s):
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def set_uri(ctrl, service, url, title="", artist=""):
    args = (
        "<InstanceID>0</InstanceID>"
        f"<CurrentURI>{_esc(url)}</CurrentURI>"
        f"<CurrentURIMetaData>{_esc(_didl(url, title, artist))}</CurrentURIMetaData>"
    )
    return _soap(ctrl, service, "SetAVTransportURI", args)


def play(ctrl, service, speed="1"):
    args = f"<InstanceID>0</InstanceID><Speed>{speed}</Speed>"
    return _soap(ctrl, service, "Play", args)


def pause(ctrl, service):
    return _soap(ctrl, service, "Pause", "<InstanceID>0</InstanceID>")


def stop(ctrl, service):
    return _soap(ctrl, service, "Stop", "<InstanceID>0</InstanceID>")


_CTRL_CACHE = {}  # ip -> (info, ts)
_CTRL_TTL = 600


def control_for(ip, timeout=3.0):
    """找到某台设备的 AVTransport 控制地址（带缓存）。"""
    import time as _t

    hit = _CTRL_CACHE.get(ip)
    if hit and _t.time() - hit[1] < _CTRL_TTL:
        return hit[0]
    info = find_device(ip, timeout=timeout)
    if info:
        _CTRL_CACHE[ip] = (info, _t.time())
    return info


def _with_retry(ip, fn):
    """设备的 DLNA 端口会变（重启后可能不同），失败时清缓存重发现一次。"""
    dev = control_for(ip)
    if not dev:
        return None, {"ok": False, "error": f"没有在 {ip} 上找到 DLNA MediaRenderer"}
    try:
        fn(dev)
        return dev, {"ok": True, "device": dev.get("friendlyName") or ip}
    except Exception as e:
        _CTRL_CACHE.pop(ip, None)
        dev2 = control_for(ip)
        if not dev2:
            return None, {"ok": False, "error": f"投放失败：{e}"}
        try:
            fn(dev2)
            return dev2, {
                "ok": True,
                "device": dev2.get("friendlyName") or ip,
                "rediscovered": True,
            }
        except Exception as e2:
            return None, {"ok": False, "error": f"投放失败：{e2}"}


def cast(ip, url, title="", artist=""):
    """一步到位：找到设备 → SetAVTransportURI → Play。"""

    def act(dev):
        set_uri(dev["control"], dev["serviceType"], url, title, artist)
        play(dev["control"], dev["serviceType"])

    dev, result = _with_retry(ip, act)
    if dev and result.get("ok"):
        result["control"] = dev["control"]
    return result


def transport_pause(ip):
    def act(dev):
        pause(dev["control"], dev["serviceType"])

    _dev, result = _with_retry(ip, act)
    return result


def transport_stop(ip):
    def act(dev):
        stop(dev["control"], dev["serviceType"])

    _dev, result = _with_retry(ip, act)
    return result
