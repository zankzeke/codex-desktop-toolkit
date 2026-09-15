import json
import zipfile
from pathlib import Path

import tomlkit

import config_manager
import history_fixer
import network_tools
import support_bundle
from error_classifier import classify_error
from runtime_stats import RuntimeStats


def test_transport_mode_round_trip(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    config.write_text('model_provider = "openai"\n', encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config)
    monkeypatch.setattr(config_manager, "STATE_PATH", state)

    ok, _ = config_manager.enable_proxy_config(8787, "http")
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["model_providers"]["openai-idfix"]["supports_websockets"] is False
    status = config_manager.get_proxy_config_status(8787)
    assert status["transport_mode"] == "http"
    assert status["supports_websockets"] is False

    ok, _ = config_manager.enable_proxy_config(8787, "auto")
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["model_providers"]["openai-idfix"]["supports_websockets"] is True
    assert config_manager.load_toolkit_settings()["transport_mode"] == "auto"


def test_transport_labels_are_stable():
    assert network_tools.transport_mode_from_label("自动") == "auto"
    assert network_tools.transport_mode_from_label("WebSocket") == "websocket"
    assert network_tools.transport_mode_from_label("强制 HTTP") == "http"
    assert network_tools.transport_label("http") == "强制 HTTP"


def test_websocket_circuit_breaker_trips_and_resets():
    stats = RuntimeStats()
    stats.ws_failed("websocket timeout")
    stats.ws_failed("websocket timeout")
    assert stats.snapshot()["websocket"]["breaker_tripped"] is False
    stats.ws_failed("websocket timeout")
    snap = stats.snapshot()["websocket"]
    assert snap["consecutive_failures"] == 3
    assert snap["breaker_tripped"] is True
    stats.ws_connected()
    snap = stats.snapshot()["websocket"]
    assert snap["consecutive_failures"] == 0
    assert snap["breaker_tripped"] is False


def test_error_classifier_exposes_next_action():
    assert classify_error(502, "websocket timed out")["next_action"] == "force_http"
    assert classify_error(400, "invalid_id_prefix")["next_action"] == "scan_sessions"


def test_backup_history_diff_and_restore(tmp_path: Path):
    original = tmp_path / "rollout.jsonl"
    old_id = "item_1234567890abcdef"
    new_id = "msg_1234567890abcdef"
    original.write_text(json.dumps({"id": old_id, "type": "message"}) + "\n", encoding="utf-8")
    backup = history_fixer.backup_file(original)
    assert backup is not None
    original.write_text(json.dumps({"id": new_id, "type": "message"}) + "\n", encoding="utf-8")

    items = history_fixer.list_session_backups(tmp_path)
    assert items and items[0]["original"] == original
    diff = history_fixer.compare_backup_ids(backup, original)
    assert old_id in diff["removed_ids"]
    assert new_id in diff["added_ids"]

    restored = history_fixer.restore_session_backup(backup, original)
    assert restored == original
    assert old_id in original.read_text(encoding="utf-8")


def test_support_bundle_redacts_sensitive_values(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        'api_key = "super-secret-value"\nbase_url = "https://user:pass@example.test/v1?q=secret"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(support_bundle, "CONFIG_PATH", config)
    monkeypatch.setattr(
        support_bundle,
        "get_diagnostics",
        lambda _port: {
            "codex": {"running": True, "version": "1", "path": str(Path.home() / "Codex.exe"), "config": {}},
            "proxy": {"running": True, "port": 8787, "stats": {"last_error": None}},
            "network": {"no_proxy": "localhost,secret.internal"},
        },
    )
    monkeypatch.setattr(support_bundle, "build_diagnostic_report", lambda _d, _v: "safe report")

    out = support_bundle.create_support_bundle(
        port=8787,
        app_version="0.5.0",
        output_path=tmp_path / "support.zip",
        recent_log_text="Authorization: Bearer abc\nhttps://user:pass@example.test/x?q=1",
    )
    with zipfile.ZipFile(out) as zf:
        cfg = zf.read("config-redacted.toml").decode()
        env = zf.read("environment.json").decode()
        errors = zf.read("recent-errors.txt").decode()
    assert "super-secret-value" not in cfg
    assert "user:pass" not in cfg
    assert "q=secret" not in cfg
    assert "secret.internal" not in env
    assert "Bearer abc" not in errors
    assert "user:pass" not in errors
