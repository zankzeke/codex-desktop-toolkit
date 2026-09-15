"""Network compatibility helpers for Codex Bridge Toolkit.

The module deliberately performs unauthenticated reachability probes only. It
never reads or replays Codex credentials.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import urllib.parse
import urllib.request
from typing import Any, Iterable

import aiohttp

from config_manager import (
    TRANSPORT_AUTO,
    TRANSPORT_HTTP,
    TRANSPORT_WEBSOCKET,
    normalize_transport_mode,
)
from process_utils import get_port_owner

TRANSPORT_LABELS = {
    TRANSPORT_AUTO: "自动",
    TRANSPORT_WEBSOCKET: "WebSocket",
    TRANSPORT_HTTP: "强制 HTTP",
}
LABEL_TO_TRANSPORT = {v: k for k, v in TRANSPORT_LABELS.items()}

COMMON_PROXY_PORTS = (
    (7897, "Clash Verge Rev / Mihomo Mixed"),
    (7890, "Clash / Mihomo Mixed"),
    (10809, "v2rayN HTTP"),
    (10808, "v2rayN / NekoBox"),
    (2081, "NekoRay HTTP"),
    (1080, "SOCKS/HTTP 常见端口"),
    (20171, "Mihomo / Clash 常见端口"),
)


def transport_label(mode: str | bool | None) -> str:
    return TRANSPORT_LABELS[normalize_transport_mode(mode)]


def transport_mode_from_label(label: str | None) -> str:
    return LABEL_TO_TRANSPORT.get(str(label or "").strip(), normalize_transport_mode(label))


def discover_local_proxies(ports: Iterable[tuple[int, str]] | None = None) -> list[dict[str, Any]]:
    """Return listening common localhost proxy ports with process metadata."""
    found: list[dict[str, Any]] = []
    for port, label in (ports or COMMON_PROXY_PORTS):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.18)
            try:
                ok = sock.connect_ex(("127.0.0.1", int(port))) == 0
            except OSError:
                ok = False
        if not ok:
            continue
        pid, owner = get_port_owner(int(port))
        owner_text = owner or "未知进程"
        found.append({
            "url": f"http://127.0.0.1:{int(port)}",
            "port": int(port),
            "label": label,
            "pid": pid,
            "process": owner_text,
            "display": f"{label} · {owner_text} · :{int(port)}",
        })
    return found


def _local_json(port: int, path: str, timeout: float = 1.5) -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{int(port)}{path}",
            headers={"User-Agent": "CodexBridgeToolkit/network-check"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 200:
                return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return None


def _route_url(upstream: str, route: str) -> str:
    parsed = urllib.parse.urlsplit(upstream.strip())
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}/{route.lstrip('/')}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


async def _probe_http_async(
    upstream: str,
    proxy_mode: str,
    explicit_proxy: str | None,
    timeout: float,
) -> dict[str, Any]:
    url = _route_url(upstream, "models")
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(
            timeout=client_timeout,
            trust_env=proxy_mode == "env",
            headers={"User-Agent": "CodexBridgeToolkit/network-check"},
        ) as session:
            async with session.get(
                url,
                allow_redirects=False,
                proxy=explicit_proxy if proxy_mode == "explicit" else None,
            ) as response:
                # 401/403/404 are still useful: the network path reached the server.
                return {
                    "reachable": True,
                    "status": int(response.status),
                    "note": "已到达上游；未携带 Codex 凭据，认证类状态码属于正常探测结果。",
                }
    except asyncio.TimeoutError:
        return {"reachable": False, "status": None, "error": "timeout"}
    except aiohttp.ClientError as exc:
        return {"reachable": False, "status": None, "error": type(exc).__name__}
    except Exception as exc:  # pragma: no cover - defensive platform edge
        return {"reachable": False, "status": None, "error": type(exc).__name__}


async def _probe_ws_async(
    upstream: str,
    proxy_mode: str,
    explicit_proxy: str | None,
    timeout: float,
) -> dict[str, Any]:
    url = _route_url(upstream, "responses")
    if url.startswith("https://"):
        url = "wss://" + url[8:]
    elif url.startswith("http://"):
        url = "ws://" + url[7:]
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(
            timeout=client_timeout,
            trust_env=proxy_mode == "env",
            headers={"User-Agent": "CodexBridgeToolkit/network-check"},
        ) as session:
            async with session.ws_connect(
                url,
                proxy=explicit_proxy if proxy_mode == "explicit" else None,
                heartbeat=15,
            ):
                return {
                    "reachable": True,
                    "established": True,
                    "status": 101,
                    "note": "未携带 Codex 凭据的 WebSocket 探测仍成功完成升级。",
                }
    except aiohttp.WSServerHandshakeError as exc:
        # A concrete HTTP handshake response proves DNS/TLS/proxy routing reached
        # the upstream, even when auth or endpoint policy rejects this anonymous probe.
        return {
            "reachable": True,
            "established": False,
            "status": int(exc.status),
            "note": "已到达上游，但匿名 WebSocket 探测未获 101；以真实 Codex 流量统计为准。",
        }
    except asyncio.TimeoutError:
        return {"reachable": False, "established": False, "status": None, "error": "timeout"}
    except aiohttp.ClientError as exc:
        return {
            "reachable": False,
            "established": False,
            "status": None,
            "error": type(exc).__name__,
        }
    except Exception as exc:  # pragma: no cover
        return {
            "reachable": False,
            "established": False,
            "status": None,
            "error": type(exc).__name__,
        }


async def _run_probes_async(
    upstream: str,
    proxy_mode: str,
    explicit_proxy: str | None,
    timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return await asyncio.gather(
        _probe_http_async(upstream, proxy_mode, explicit_proxy, timeout),
        _probe_ws_async(upstream, proxy_mode, explicit_proxy, timeout),
    )


def run_network_check(
    *,
    upstream: str,
    proxy_mode: str = "direct",
    explicit_proxy: str | None = None,
    local_port: int = 8787,
    transport_mode: str = TRANSPORT_AUTO,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Run a privacy-safe Codex network compatibility check."""
    mode = normalize_transport_mode(transport_mode)
    details = _local_json(local_port, "/health/details")
    stats = _local_json(local_port, "/stats") or {}
    ws_stats = stats.get("websocket", {}) or {}

    try:
        http_probe, ws_probe = asyncio.run(
            _run_probes_async(upstream, proxy_mode, explicit_proxy, timeout)
        )
    except RuntimeError:
        # Primarily for test/embedding environments that already own an event loop.
        http_probe = {"reachable": False, "status": None, "error": "event-loop-busy"}
        ws_probe = {
            "reachable": False,
            "established": False,
            "status": None,
            "error": "event-loop-busy",
        }

    recommendations: list[str] = []
    summary = "网络路径未完成验证"

    ws_failures = int(ws_stats.get("failures", 0) or 0)
    ws_handshakes = int(ws_stats.get("handshakes", 0) or 0)
    consecutive = int(ws_stats.get("consecutive_failures", 0) or 0)
    breaker = bool(ws_stats.get("breaker_tripped"))

    if mode == TRANSPORT_HTTP:
        if http_probe.get("reachable"):
            summary = "强制 HTTP 模式：上游 HTTP 路径可达"
        else:
            summary = "强制 HTTP 模式：上游 HTTP 路径不可达"
            recommendations.append("检查 VPN/TUN、DNS 或 Toolkit 的上游代理设置。")
    elif breaker or consecutive >= 3:
        summary = "检测到连续 WebSocket 失败，建议暂时切换强制 HTTP"
        recommendations.append("在传输模式中选择“强制 HTTP”，可跳过 Codex 的重复 WS 重连等待。")
    elif ws_handshakes > 0 and ws_failures == 0:
        summary = "已由真实 Codex 流量验证 WebSocket 正常"
    elif http_probe.get("reachable") and not ws_probe.get("reachable"):
        summary = "HTTP 可达，但 WebSocket 网络路径失败"
        recommendations.append("优先检查非全局代理、TUN 覆盖和节点对长连接/WebSocket 的支持。")
        recommendations.append("如果 HTTP 一直正常，可使用“强制 HTTP”避免反复重连。")
    elif http_probe.get("reachable") and ws_probe.get("reachable"):
        summary = "HTTP 与 WebSocket 网络路径均已到达上游"
        if not ws_probe.get("established") and ws_handshakes == 0:
            recommendations.append("匿名探测无法验证完整 Codex WS 会话；启动 Codex 后观察“真实流量”统计。")
    else:
        summary = "HTTP 与 WebSocket 均未确认可达"
        recommendations.append("检查当前代理模式、VPN/TUN、DNS 和上游代理端口是否可用。")

    last_error = stats.get("last_error") or {}
    if last_error.get("category") == "capacity":
        recommendations.append("最近错误属于上游模型容量问题，本地代理无法修复，稍后重试即可。")
    elif last_error.get("category") in {"ws_timeout", "ws_abnormal", "network"}:
        recommendations.append(str(last_error.get("action") or "检查网络/代理设置。"))

    return {
        "summary": summary,
        "transport_mode": mode,
        "local_proxy_running": details is not None,
        "local_details": details or {},
        "runtime_stats": stats,
        "http_probe": http_probe,
        "websocket_probe": ws_probe,
        "environment": {
            "HTTP_PROXY": bool(os.environ.get("HTTP_PROXY")),
            "HTTPS_PROXY": bool(os.environ.get("HTTPS_PROXY")),
            "ALL_PROXY": bool(os.environ.get("ALL_PROXY")),
            "NO_PROXY": bool(os.environ.get("NO_PROXY")),
        },
        "recommendations": recommendations,
    }


def format_network_check(result: dict[str, Any]) -> str:
    """Human-readable Chinese summary for the GUI."""
    hp = result.get("http_probe") or {}
    wp = result.get("websocket_probe") or {}
    stats = result.get("runtime_stats") or {}
    ws = stats.get("websocket") or {}
    lines = [
        "====== Codex 网络体检 ======",
        "",
        f"结论: {result.get('summary') or '未知'}",
        f"传输模式: {transport_label(result.get('transport_mode'))}",
        f"本地代理: {'运行中' if result.get('local_proxy_running') else '未运行/未检测到'}",
        f"HTTP 上游路径: {'可达' if hp.get('reachable') else '失败'}"
        + (f" (HTTP {hp.get('status')})" if hp.get('status') is not None else ""),
        f"WebSocket 路径: {'可达' if wp.get('reachable') else '失败'}"
        + (" / 已完成 101 升级" if wp.get('established') else "")
        + (f" (HTTP {wp.get('status')})" if wp.get('status') is not None else ""),
        f"真实 WS 握手: {ws.get('handshakes', 0)}",
        f"真实 WS 失败: {ws.get('failures', 0)}",
        f"连续 WS 失败: {ws.get('consecutive_failures', 0)}",
        f"熔断建议: {'已触发' if ws.get('breaker_tripped') else '未触发'}",
        "",
        "建议:",
    ]
    recs = result.get("recommendations") or ["暂无额外建议。"]
    lines.extend(f"  • {item}" for item in recs)
    lines.append("")
    lines.append("说明：主动探测不会读取或发送 Codex 的 Authorization/Cookie。")
    return "\n".join(lines)
