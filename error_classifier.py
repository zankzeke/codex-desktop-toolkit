"""Privacy-safe error classification for Codex Bridge Toolkit.

The classifier deliberately returns structured ErrorDiagnosis objects.
It never persists request/response bodies or authentication data.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Iterator


@dataclass
class ErrorDiagnosis(Mapping):
    category: str
    title: str
    explanation: str
    recommended_action: str
    action_id: str
    status: int | None = None

    def __getitem__(self, key: str) -> Any:
        if key == "action":
            return self.recommended_action
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (KeyError, AttributeError):
            return default

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.recommended_action
        return d


ERROR_CATALOG: dict[str, tuple[str, str, str, str]] = {
    # category: (title, explanation, recommended_action, action_id)
    "ok": (
        "正常",
        "请求处理成功。",
        "无。",
        "none",
    ),
    "invalid_id": (
        "会话 ID 错误",
        "会话中残留了第三方或旧版生成的不兼容合成 ID (如 item_xxx / resp_xxx_msg)。",
        "扫描并修复受损的会话文件，然后再与 Codex 进行交互。",
        "scan_sessions",
    ),
    "ws_timeout": (
        "WebSocket 握手超时",
        "Codex 与上游服务建立 WebSocket 连接时超时，通常由代理或网络链路不稳定导致。",
        "运行网络体检排查连接，或切换为「强制 HTTP」避免反复重试。",
        "open_net_diagnostics",
    ),
    "ws_abnormal": (
        "WebSocket 异常中断 (1006)",
        "WebSocket 连接在未收到正常关闭帧的情况下异常断开，可能是代理/VPN/TUN 中断了会话。",
        "建议切换为「强制 HTTP」模式，跳过不稳定的 WebSocket 链路。",
        "switch_force_http",
    ),
    "ws_policy": (
        "WebSocket 策略拒绝 (1008)",
        "上游以 Policy Violation (1008) 关闭了连接，通常与协议参数、身份状态或中间层有关。",
        "建议切换为「强制 HTTP」或检查账号登录状态。",
        "switch_force_http",
    ),
    "ws_internal": (
        "WebSocket 内部错误 (1011)",
        "上游服务处理 WebSocket 时遇到内部异常。",
        "建议切换为「强制 HTTP」模式或稍后重试。",
        "switch_force_http",
    ),
    "proxy_connect_error": (
        "代理连接失败",
        "Toolkit 无法连接到配置的科学上网代理软件 (如 127.0.0.1:7890)。",
        "打开「本地代理检测」检查 Clash / v2rayN 是否正常开启并监听对应端口。",
        "open_proxy_discovery",
    ),
    "network": (
        "网络/上游连接受阻",
        "本地代理无法与上游服务器建立 TCP/TLS 连接或 DNS 解析失败。",
        "打开「网络体检」检查本地网络、代理软件与节点状态。",
        "open_net_diagnostics",
    ),
    "auth": (
        "认证/权限失败 (401/403)",
        "请求被上游拒绝认证，可能因为使用了无效的 API Key、登录过期或上游不支持该调用方式。",
        "检查 Codex 登录状态；若使用了第三方中转，建议核对 Key 或恢复官方配置。",
        "restore_config",
    ),
    "capacity": (
        "上游模型容量不足",
        "OpenAI 服务器当前负载过高 (Selected model is at capacity)。",
        "此故障属于 OpenAI 官方服务端拥堵，本地工具无法强制修复，请稍后重试或切换模型。",
        "none",
    ),
    "rate_limit": (
        "请求被限流 (429)",
        "触发了频率限制 (Rate limit reached) 或账号额度已耗尽。",
        "请等待限流时间窗口重置，或检查账号余额与用量。",
        "none",
    ),
    "upstream_5xx": (
        "上游服务异常 (5xx)",
        "上游服务器返回了 5xx 内部错误，属于远端服务故障。",
        "通常无需修改本地配置或会话，请等待官方或中转站服务恢复。",
        "none",
    ),
    "client_4xx": (
        "请求格式被拒绝 (4xx)",
        "上游返回了客户端请求错误 (4xx)。",
        "查看脱敏诊断报告和日志以排查请求格式与参数。",
        "none",
    ),
    "unknown": (
        "未知错误",
        "遇到了未分类的请求状态。",
        "查看脱敏诊断报告和最近日志定位。",
        "none",
    ),
}


def _result(category: str, status: int | None = None) -> ErrorDiagnosis:
    meta = ERROR_CATALOG.get(category, ERROR_CATALOG["unknown"])
    title, explanation, action, action_id = meta
    return ErrorDiagnosis(
        category=category,
        title=title,
        explanation=explanation,
        recommended_action=action,
        action_id=action_id,
        status=status,
    )


def classify_error(
    status: int | None = None,
    message: str | None = None,
    *,
    event_type: str | None = None,
) -> ErrorDiagnosis:
    """Classify an HTTP/SSE error without returning the original message."""
    text = " ".join(filter(None, (message, event_type))).lower()

    if "selected model is at capacity" in text or "at capacity" in text or "server overloaded" in text:
        return _result("capacity", status)
    if "invalid_id_prefix" in text or "expected an id that begins with" in text or "synthetic" in text:
        return _result("invalid_id", status)
    if "rate limit" in text or "usage limit" in text or "too many requests" in text or status == 429:
        return _result("rate_limit", status)
    if status in (401, 403) or any(x in text for x in ("unauthorized", "forbidden", "invalid api key", "authentication")):
        return _result("auth", status)
    if any(x in text for x in ("proxy connect", "proxy connection", "proxy error", "connect to proxy")):
        return _result("proxy_connect_error", status)
    if any(x in text for x in ("websocket", "ws ")) and any(x in text for x in ("timeout", "timed out")):
        return _result("ws_timeout", status)
    if status == 502 or any(x in text for x in ("connection failed", "connection error", "cannot connect", "dns", "connection refused")):
        return _result("network", status)
    if status is not None and 500 <= status <= 599:
        return _result("upstream_5xx", status)
    if status is not None and 400 <= status <= 499:
        return _result("client_4xx", status)
    return _result("unknown", status)


def classify_ws_close(code: int | None) -> ErrorDiagnosis:
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
