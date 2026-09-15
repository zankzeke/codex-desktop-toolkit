from __future__ import annotations

from error_classifier import ErrorDiagnosis, classify_error, classify_ws_close


def test_error_diagnosis_action_ids():
    assert classify_error(message="invalid_id_prefix found").action_id == "scan_sessions"
    assert classify_error(message="ws timeout connecting").action_id == "open_net_diagnostics"
    assert classify_ws_close(1006).action_id == "switch_force_http"
    assert classify_ws_close(1008).action_id == "switch_force_http"
    assert classify_error(message="proxy connection error").action_id == "open_proxy_discovery"
    assert classify_error(status=502).action_id == "open_net_diagnostics"
    assert classify_error(status=401).action_id == "restore_config"
    assert classify_error(message="model at capacity").action_id == "none"
    assert classify_error(status=429).action_id == "none"
    assert classify_error(status=500).action_id == "none"


def test_error_diagnosis_backwards_compatibility():
    diag = classify_error(status=401)
    assert isinstance(diag, ErrorDiagnosis)
    # Dict-like access
    assert diag["category"] == "auth"
    assert "认证" in diag["title"]
    assert diag["action"] == diag.recommended_action
    assert diag.get("status") == 401
    assert diag.get("nonexistent", "fallback") == "fallback"
    # to_dict
    d = diag.to_dict()
    assert isinstance(d, dict)
    assert d["action_id"] == "restore_config"
