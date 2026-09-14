from pathlib import Path

from error_classifier import classify_error, classify_ws_close
from runtime_stats import RuntimeStats
from update_checker import is_newer


def test_error_classification():
    assert classify_error(503, "Selected model is at capacity")["category"] == "capacity"
    assert classify_error(429, "too many requests")["category"] == "rate_limit"
    assert classify_error(400, "invalid_id_prefix")["category"] == "invalid_id"
    assert classify_ws_close(1008)["category"] == "ws_policy"
    assert classify_ws_close(1006)["category"] == "ws_abnormal"


def test_runtime_stats_marks_real_traffic_and_ws():
    stats = RuntimeStats()
    assert stats.snapshot()["traffic_verified"] is False
    stats.record_request("POST", "/v1/responses?secret=nope")
    stats.record_response(200)
    stats.record_rewrite(2, 1)
    stats.ws_connected()
    stats.ws_closed(1000)
    snap = stats.snapshot()
    assert snap["traffic_verified"] is True
    assert snap["last_request_path"] == "/v1/responses"
    assert snap["id_fixes_total"] == 2
    assert snap["reasoning_drops_total"] == 1
    assert snap["websocket"]["handshakes"] == 1


def test_version_compare():
    assert is_newer("0.4.1", "0.4.0")
    assert not is_newer("0.4.0", "0.4.0")
    assert not is_newer("0.3.9", "0.4.0")


def test_gui_contains_three_layer_status_tray_and_one_click_launch():
    text = Path("codex_toolkit_gui.py").read_text(encoding="utf-8")
    assert "实际流量：已验证" in text
    assert "self._tray.hide_window_to_tray()" in text
    assert "通过代理启动 Codex" in text
    assert 'proxy_mode = "env"' in text


def test_proxy_has_stats_endpoint_and_direct_mode():
    text = Path("proxy.py").read_text(encoding="utf-8")
    assert 'app.router.add_get("/stats", proxy.handle_stats)' in text
    assert 'trust_env=self.proxy_mode == "env"' in text
    assert '--proxy-mode' in text
