"""proxy_discovery.py — Automatic detection and validation of local proxy software.

Detects running proxy applications (Clash Verge Rev, Clash, Mihomo, v2rayN,
NekoRay, sing-box) and their listening ports with actual connectivity testing.
"""
from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any, Tuple

import aiohttp

from process_utils import get_port_owner

KNOWN_PROXY_SIGNATURES: dict[str, str] = {
    "clash-verge": "Clash Verge Rev",
    "clashverge": "Clash Verge Rev",
    "clash": "Clash",
    "mihomo": "Mihomo",
    "v2rayn": "v2rayN",
    "v2ray": "v2rayN",
    "nekoray": "NekoRay",
    "nekobox": "NekoBox",
    "sing-box": "sing-box",
    "singbox": "sing-box",
    "shadowsocks": "Shadowsocks",
}

COMMON_PROXY_PORTS: list[int] = [
    7897,   # Clash Verge Rev (default mixed)
    7890,   # Clash / Mihomo (default mixed)
    7891,   # Clash (common alternate)
    10809,  # v2rayN (default HTTP)
    10808,  # v2rayN (default SOCKS5, some listen HTTP)
    2081,   # NekoRay (default HTTP)
    20171,  # sing-box / GUI (common mixed)
    1080,   # Shadowsocks / Generic socks/http
    8080,   # Generic HTTP proxy
]


@dataclass
class DiscoveredProxy:
    name: str
    host: str
    port: int
    proxy_url: str
    process_name: str | None
    pid: int | None
    identified: bool
    status: str = "未测试"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """Test if a local TCP port is actively listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def match_process_name(process_name: str | None) -> str | None:
    """Match a Windows process name to a known proxy application."""
    if not process_name:
        return None
    normalized = process_name.lower().replace(".exe", "").strip()
    for sig, label in KNOWN_PROXY_SIGNATURES.items():
        if sig in normalized:
            return label
    return None


def discover_proxies(
    candidate_ports: list[int] | None = None,
    exclude_port: int | None = None,
) -> list[DiscoveredProxy]:
    """Scan candidate ports and active processes to discover local proxies.

    *exclude_port* avoids discovering Toolkit's own listening port.
    """
    ports = candidate_ports or COMMON_PROXY_PORTS
    results: list[DiscoveredProxy] = []
    seen_ports: set[int] = set()

    for port in ports:
        if exclude_port and port == exclude_port:
            continue
        if port in seen_ports:
            continue

        if is_port_listening(port):
            seen_ports.add(port)
            pid, proc_name = get_port_owner(port)

            # Skip Toolkit's own proxy executable if detected
            if proc_name and "codexbridgeproxy" in proc_name.lower():
                continue

            app_label = match_process_name(proc_name)
            identified = bool(app_label)
            display_name = app_label if identified else f"未识别本地代理 (端口 {port})"

            results.append(
                DiscoveredProxy(
                    name=display_name,
                    host="127.0.0.1",
                    port=port,
                    proxy_url=f"http://127.0.0.1:{port}",
                    process_name=proc_name,
                    pid=pid,
                    identified=identified,
                )
            )

    return results


async def test_proxy_http_async(
    proxy_url: str,
    target_url: str = "https://chatgpt.com/backend-api/codex",
    timeout: float = 4.0,
) -> Tuple[bool, float, str]:
    """Test HTTP connectivity through a local proxy."""
    t0 = time.monotonic()
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=timeout * 0.7)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.get(target_url, proxy=proxy_url) as resp:
                elapsed = round((time.monotonic() - t0) * 1000, 1)
                # Any response code < 500 confirms HTTP proxy forwarding works
                if resp.status < 500:
                    return True, elapsed, f"HTTP 连通正常 (状态码 {resp.status})"
                return False, elapsed, f"上游服务异常 (HTTP {resp.status})"
    except asyncio.TimeoutError:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        return False, elapsed, "连接超时 (Timeout)"
    except Exception as exc:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        return False, elapsed, f"连接失败: {type(exc).__name__}"


async def test_proxy_ws_async(
    proxy_url: str,
    target_url: str = "https://chatgpt.com/backend-api/codex",
    timeout: float = 4.0,
) -> Tuple[bool, float, str]:
    """Test WebSocket handshake capability through a local proxy."""
    ws_url = target_url
    if ws_url.startswith("https://"):
        ws_url = "wss://" + ws_url[8:]
    elif ws_url.startswith("http://"):
        ws_url = "ws://" + ws_url[7:]
    ws_url = ws_url.rstrip("/") + "/responses"

    t0 = time.monotonic()
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=timeout * 0.7)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.ws_connect(
                ws_url,
                proxy=proxy_url,
                protocols=("codex-probe",),
            ) as ws:
                elapsed = round((time.monotonic() - t0) * 1000, 1)
                await ws.close()
                return True, elapsed, "WebSocket 握手正常"
    except aiohttp.WSServerHandshakeError as exc:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        if exc.status in (401, 403):
            # The handshake reached upstream and rejected unauthenticated probe -> WS forwarding works!
            return True, elapsed, f"WebSocket 握手成功 (HTTP {exc.status} 鉴权正常)"
        return False, elapsed, f"握手被拒绝 (HTTP {exc.status})"
    except asyncio.TimeoutError:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        return False, elapsed, "WebSocket 握手超时 (Timeout)"
    except Exception as exc:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        return False, elapsed, f"WebSocket 失败: {type(exc).__name__}"


def test_proxy_http(proxy_url: str, target_url: str = "https://chatgpt.com/backend-api/codex", timeout: float = 4.0) -> Tuple[bool, float, str]:
    """Synchronous wrapper for test_proxy_http_async."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(test_proxy_http_async(proxy_url, target_url, timeout))
    finally:
        loop.close()


def test_proxy_ws(proxy_url: str, target_url: str = "https://chatgpt.com/backend-api/codex", timeout: float = 4.0) -> Tuple[bool, float, str]:
    """Synchronous wrapper for test_proxy_ws_async."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(test_proxy_ws_async(proxy_url, target_url, timeout))
    finally:
        loop.close()
