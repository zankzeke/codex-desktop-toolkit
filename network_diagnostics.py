"""network_diagnostics.py — Diagnostic checks for Codex HTTPS & WebSocket connectivity.

Tests local proxy, upstream HTTPS, WebSocket handshake, and system proxy settings.
Produces structured diagnostic results with actionable recommendations.
"""
from __future__ import annotations

import asyncio
import os
import socket
import ssl
import time
import urllib.request
import winreg
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp


def mask_url_sensitive(url: str | None) -> str:
    """Safely redact credentials, query strings, and fragments from a URL."""
    if not url:
        return "unset"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        if parsed.username is not None or parsed.password is not None:
            host = f"***:***@{host}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except Exception:
        return "<redacted-url>"


def get_windows_internet_settings() -> dict[str, Any]:
    """Read Windows system proxy configuration from the registry."""
    result = {"proxy_enabled": False, "proxy_server": "unset", "error": None}
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_READ,
        ) as key:
            try:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
                result["proxy_enabled"] = bool(enabled)
            except FileNotFoundError:
                pass
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                result["proxy_server"] = mask_url_sensitive(str(server)) if server else "unset"
            except FileNotFoundError:
                pass
    except Exception as exc:
        result["error"] = str(exc)
    return result


def check_system_proxy_env() -> dict[str, Any]:
    """Inspect environment proxy variables with sensitive parts redacted."""
    win_settings = get_windows_internet_settings()
    no_proxy_val = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    return {
        "http_proxy": mask_url_sensitive(os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")),
        "https_proxy": mask_url_sensitive(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
        "all_proxy": mask_url_sensitive(os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")),
        "no_proxy_set": bool(no_proxy_val),
        "windows_settings": win_settings,
    }


def check_local_proxy_health(port: int) -> dict[str, Any]:
    """Probe the local Codex Bridge proxy health and configuration status."""
    res = {
        "listening": False,
        "health_ok": False,
        "port": port,
        "latency_ms": 0.0,
        "details": {},
        "error": None,
    }

    t0 = time.monotonic()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                res["listening"] = True
    except Exception as exc:
        res["error"] = f"Socket check failed: {exc}"
        return res

    if not res["listening"]:
        res["error"] = f"本地端口 {port} 未在监听"
        return res

    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health/details")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            res["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
            if resp.status == 200:
                import json
                res["health_ok"] = True
                res["details"] = json.loads(resp.read().decode("utf-8"))
    except Exception:
        # Fallback to simple /health
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                res["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
                res["health_ok"] = (resp.status == 200)
        except Exception as exc:
            res["error"] = f"Health endpoint failed: {exc}"

    return res


async def check_https_connectivity_async(
    target_url: str = "https://chatgpt.com/backend-api/codex",
    timeout: float = 6.0,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Test TCP, TLS, and HTTP probing to upstream without sending credentials or chat content."""
    res: dict[str, Any] = {
        "ok": False,
        "target": mask_url_sensitive(target_url),
        "status": None,
        "dns_ms": None,
        "connect_ms": None,
        "total_ms": None,
        "error": None,
        "error_type": None,
    }

    parsed = urlsplit(target_url)
    host = parsed.hostname or "chatgpt.com"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # 1. DNS Timing
    t0 = time.monotonic()
    try:
        loop = asyncio.get_running_loop()
        await loop.getaddrinfo(host, port)
        res["dns_ms"] = round((time.monotonic() - t0) * 1000, 1)
    except Exception as exc:
        res["error"] = f"DNS 解析失败: {exc}"
        res["error_type"] = "dns_error"
        return res

    # 2. HTTP Probe via aiohttp
    t_start = time.monotonic()
    try:
        timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=timeout * 0.7)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            # We perform a GET with harmless headers
            headers = {"User-Agent": "CodexBridgeToolkit-Diagnostics/0.5.0"}
            async with session.get(target_url, headers=headers, proxy=proxy_url) as resp:
                res["status"] = resp.status
                res["total_ms"] = round((time.monotonic() - t_start) * 1000, 1)
                # Any response code from the server (even 401/403/404) proves network TCP+TLS reachability!
                if resp.status < 500:
                    res["ok"] = True
                else:
                    res["ok"] = False
                    res["error"] = f"上游服务返回 HTTP {resp.status}"
                    res["error_type"] = "upstream_5xx"
    except asyncio.TimeoutError:
        res["error"] = "连接超时 (Timeout)"
        res["error_type"] = "timeout"
        res["total_ms"] = round((time.monotonic() - t_start) * 1000, 1)
    except aiohttp.ClientConnectorError as exc:
        res["error"] = f"无法建立连接: {exc.os_error or exc}"
        res["error_type"] = "connect_error"
    except aiohttp.ClientSSLError as exc:
        res["error"] = f"TLS/SSL 证书或握手失败: {exc}"
        res["error_type"] = "ssl_error"
    except Exception as exc:
        res["error"] = f"请求异常: {exc}"
        res["error_type"] = type(exc).__name__

    return res


async def check_websocket_connectivity_async(
    target_url: str = "https://chatgpt.com/backend-api/codex",
    timeout: float = 6.0,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Probe WebSocket upgrade without sending user chat content."""
    res: dict[str, Any] = {
        "ok": False,
        "target": mask_url_sensitive(target_url),
        "status": None,
        "handshake_ms": None,
        "protocol": None,
        "close_code": None,
        "error": None,
        "error_type": None,
    }

    # Derive wss:// URL
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
                timeout=timeout,
                protocols=("codex-diagnostics",),
            ) as ws:
                res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
                res["ok"] = True
                res["protocol"] = ws.protocol
                await ws.close()
    except aiohttp.WSServerHandshakeError as exc:
        res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["status"] = exc.status
        if exc.status in (401, 403):
            # Endpoint reached and rejected unauthenticated probe - network & WS handshake path works!
            res["ok"] = True
            res["error"] = f"握手通过，上游要求认证 (HTTP {exc.status})"
            res["error_type"] = "auth_required"
        elif exc.status >= 500:
            res["ok"] = False
            res["error"] = f"上游服务拒绝 WebSocket 握手 (HTTP {exc.status})"
            res["error_type"] = f"http_{exc.status}"
        else:
            res["ok"] = False
            res["error"] = f"WebSocket 握手失败: HTTP {exc.status}"
            res["error_type"] = f"http_{exc.status}"
    except asyncio.TimeoutError:
        res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["error"] = "WebSocket 握手超时 (Timeout)"
        res["error_type"] = "ws_timeout"
    except Exception as exc:
        res["handshake_ms"] = round((time.monotonic() - t0) * 1000, 1)
        res["error"] = f"WebSocket 异常: {exc}"
        res["error_type"] = type(exc).__name__

    return res


def evaluate_diagnostics(
    local_res: dict[str, Any],
    https_res: dict[str, Any],
    ws_res: dict[str, Any],
    sys_proxy: dict[str, Any],
) -> dict[str, Any]:
    """Compare HTTP vs WS results to form clear conclusions and recommendations."""
    https_ok = bool(https_res.get("ok"))
    ws_ok = bool(ws_res.get("ok"))

    https_status = f"正常 ({https_res.get('total_ms')}ms)" if https_ok else f"失败: {https_res.get('error')}"
    ws_status = f"正常 ({ws_res.get('handshake_ms')}ms)" if ws_ok else f"失败: {ws_res.get('error')}"

    recommendations: list[str] = []

    if not local_res.get("listening"):
        conclusion = "本地 Toolkit 代理未启动或未正常监听指定端口。"
        recommendations.append("请先在「代理控制」页面启动本地代理。")
    elif https_ok and not ws_ok:
        conclusion = (
            "HTTPS 访问正常，但 WebSocket 握手失败或超时。\n"
            "这种情况通常发生在部分网络中间件、VPN、TUN 或代理软件未对长连接/WebSocket 协议提供完整转发支持。"
        )
        recommendations.append("在 Toolkit 中将传输模式切换为「强制 HTTP」以跳过反复的 WS 重连。")
        recommendations.append("若使用 Clash / Mihomo 等工具，请检查是否开启了 TUN 模式或节点支持 WebSocket。")
        recommendations.append("尝试更换科学上网节点后再次体检。")
    elif not https_ok and not ws_ok:
        # Check if both are 5xx
        h_type = https_res.get("error_type")
        w_type = ws_res.get("error_type")
        if h_type == "upstream_5xx" or (https_res.get("status") and https_res.get("status") >= 500):
            conclusion = "HTTPS 与 WebSocket 均返回 5xx 上游错误，疑似 OpenAI / 上游服务暂时不可用。"
            recommendations.append("此故障通常属于服务端临时故障，Toolkit 本地无法修复，请稍后重试。")
            recommendations.append("访问 status.openai.com 或对应中转站状态页查看故障公告。")
        elif h_type == "timeout" or w_type == "ws_timeout":
            conclusion = "HTTPS 与 WebSocket 连接均超时，本地网络无法直连上游服务器。"
            recommendations.append("请检查是否开启了科学上网代理软件（如 Clash、v2rayN）。")
            recommendations.append("在 Toolkit「代理控制」配置正确的外发代理地址（如 http://127.0.0.1:7890）。")
        else:
            conclusion = f"无法连通上游服务 ({https_res.get('error') or '网络连接受阻'})。"
            recommendations.append("检查系统 DNS 解析或本地安全防护软件是否拦截了流量。")
            recommendations.append("检查代理软件配置与节点联通性。")
    elif https_ok and ws_ok:
        conclusion = "上游网络连通良好，HTTPS 与 WebSocket 握手均正常，当前网络适合使用自动传输模式。"
        recommendations.append("无需额外调整，可正常配合 Codex Desktop 使用。")
    else:
        conclusion = "WebSocket 正常但 HTTPS 探测异常，这通常是偶发网络抖动。"
        recommendations.append("稍后重新运行体检以确认结果。")

    return {
        "https_status": https_status,
        "ws_status": ws_status,
        "conclusion": conclusion,
        "recommendations": recommendations,
    }


def run_full_diagnostics(
    port: int,
    upstream_url: str = "https://chatgpt.com/backend-api/codex",
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Synchronous orchestrator to run all diagnostic checks."""
    local_info = check_local_proxy_health(port)
    sys_proxy = check_system_proxy_env()

    # Run async network checks in an isolated event loop
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        https_task = loop.create_task(check_https_connectivity_async(upstream_url, proxy_url=proxy_url))
        ws_task = loop.create_task(check_websocket_connectivity_async(upstream_url, proxy_url=proxy_url))
        https_info, ws_info = loop.run_until_complete(asyncio.gather(https_task, ws_task))
    finally:
        loop.close()

    eval_info = evaluate_diagnostics(local_info, https_info, ws_info, sys_proxy)

    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "local": local_info,
        "system_proxy": sys_proxy,
        "https": https_info,
        "websocket": ws_info,
        "evaluation": eval_info,
    }
