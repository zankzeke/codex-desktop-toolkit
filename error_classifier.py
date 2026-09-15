"""Privacy-safe error classification for Codex Bridge Toolkit.

The classifier deliberately returns only a small category/title/action tuple. It
never persists request/response bodies or authentication data.
"""
from __future__ import annotations

from typing import Any


ERROR_INFO = {
    "ok": ("正常", "请求成功"),
    "capacity": ("上游容量", "模型当前容量不足，稍后重试或切换模型。"),
    "auth": ("认证/权限", "检查 Codex 登录状态、账号权限或上游认证。"),
    "rate_limit": ("限流/额度", "检查账号用量、速率限制或稍后重试。"),
    "invalid_id": ("会话 ID", "旧会话包含不兼容 ID，可扫描/修复会话后重试。"),
    "ws_policy": ("WebSocket 策略", "上游以 1008 Policy 关闭连接，检查账号/策略/网络中间层。"),
    "ws_abnormal": ("WebSocket 异常", "连接异常中断（1006），检查代理、VPN、TUN 或网络稳定性。"),
    "ws_internal": ("WebSocket 内部错误", "连接以 1011 结束，检查 Toolkit 日志和上游状态。"),
    "ws_timeout": ("WebSocket 超时", "WebSocket 握手或连接超时，可检查代理/TUN 或切换强制 HTTP。"),
    "network": ("网络/上游连接", "本地代理无法稳定连接上游，检查上游代理、DNS、VPN/TUN。"),
    "upstream_5xx": ("上游服务异常", "上游返回 5xx；通常无需修改本地会话，稍后重试。"),
    "client_4xx": ("请求被拒绝", "上游返回 4xx；检查配置、请求兼容性和账号状态。"),
    "unknown": ("未知错误", "查看脱敏诊断报告和最近日志定位。"),
}

NEXT_ACTION = {
    "ok": "none",
    "capacity": "none",
    "auth": "restore_direct",
    "rate_limit": "none",
    "invalid_id": "scan_sessions",
    "ws_policy": "network_check",
    "ws_abnormal": "force_http",
    "ws_internal": "network_check",
    "ws_timeout": "force_http",
    "network": "network_check",
    "upstream_5xx": "none",
    "client_4xx": "diagnostics",
    "unknown": "diagnostics",
}


def _result(category: str, status: int | None = None) -> dict[str, Any]:
    title, action = ERROR_INFO.get(category, ERROR_INFO["unknown"])
    return {
        "category": category,
        "title": title,
        "action": action,
        "next_action": NEXT_ACTION.get(category, "diagnostics"),
        "status": status,
    }


def classify_error(
    status: int | None = None,
    message: str | None = None,
    *,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Classify an HTTP/SSE error without returning the original message."""
    text = " ".join(filter(None, (message, event_type))).lower()

    if "selected model is at capacity" in text or "at capacity" in text or "server overloaded" in text:
        return _result("capacity", status)
    if "invalid_id_prefix" in text or "expected an id that begins with" in text:
        return _result("invalid_id", status)
    if "rate limit" in text or "usage limit" in text or "too many requests" in text or status == 429:
        return _result("rate_limit", status)
    if status in (401, 403) or any(x in text for x in ("unauthorized", "forbidden", "invalid api key", "authentication")):
        return _result("auth", status)
    if any(x in text for x in ("websocket", "ws ")) and any(x in text for x in ("timeout", "timed out")):
        return _result("ws_timeout", status)
    if status == 502 or any(x in text for x in ("connection failed", "connection error", "cannot connect", "dns")):
        return _result("network", status)
    if status is not None and 500 <= status <= 599:
        return _result("upstream_5xx", status)
    if status is not None and 400 <= status <= 499:
        return _result("client_4xx", status)
    return _result("unknown", status)


def classify_ws_close(code: int | None) -> dict[str, Any]:
    """Classify a WebSocket close code without retaining close reason text."""
    if code in (None, 1000, 1001):
        return _result("ok", code)
    if code == 1008:
        return _result("ws_policy", code)
    if code == 1006:
        return _result("ws_abnormal", code)
    if code == 1011:
        return _result("ws_internal", code)
    return _result("unknown", code)
