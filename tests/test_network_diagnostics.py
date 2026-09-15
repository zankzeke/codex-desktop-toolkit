from __future__ import annotations

from network_diagnostics import evaluate_diagnostics, mask_url_sensitive


def test_mask_url_sensitive():
    assert mask_url_sensitive("http://user:password123@127.0.0.1:7890/path?token=secret#frag") == "http://***:***@127.0.0.1:7890/path"
    assert mask_url_sensitive("https://api.openai.com/v1/responses?key=123") == "https://api.openai.com/v1/responses"
    assert mask_url_sensitive(None) == "unset"


def test_evaluate_diagnostics_http_ok_ws_fail():
    local_res = {"listening": True, "health_ok": True}
    https_res = {"ok": True, "total_ms": 180, "error": None}
    ws_res = {"ok": False, "handshake_ms": 2500, "error": "WebSocket 握手超时 (Timeout)", "error_type": "ws_timeout"}
    sys_proxy = {"windows_settings": {"proxy_enabled": True}}

    diag = evaluate_diagnostics(local_res, https_res, ws_res, sys_proxy)
    assert "HTTPS 访问正常，但 WebSocket 握手失败" in diag["conclusion"]
    assert any("强制 HTTP" in r for r in diag["recommendations"])


def test_evaluate_diagnostics_both_503():
    local_res = {"listening": True, "health_ok": True}
    https_res = {"ok": False, "status": 503, "error": "HTTP 503", "error_type": "upstream_5xx"}
    ws_res = {"ok": False, "status": 503, "error": "HTTP 503", "error_type": "http_503"}
    sys_proxy = {"windows_settings": {"proxy_enabled": False}}

    diag = evaluate_diagnostics(local_res, https_res, ws_res, sys_proxy)
    assert "5xx" in diag["conclusion"] or "上游服务" in diag["conclusion"]
    assert any("服务端临时故障" in r for r in diag["recommendations"])


def test_evaluate_diagnostics_both_ok():
    local_res = {"listening": True, "health_ok": True}
    https_res = {"ok": True, "total_ms": 120, "error": None}
    ws_res = {"ok": True, "handshake_ms": 150, "error": None}
    sys_proxy = {}

    diag = evaluate_diagnostics(local_res, https_res, ws_res, sys_proxy)
    assert "连通良好" in diag["conclusion"]
