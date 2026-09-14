from pathlib import Path

import tomlkit

import config_manager


def test_enable_disable_round_trip_preserves_other_sections(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    config.write_text(
        'model_provider = "custom"\n\n[model_providers.custom]\nname = "Custom"\nbase_url = "https://example.test/v1"\n\n[projects.demo]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config)
    monkeypatch.setattr(config_manager, "STATE_PATH", state)

    ok, _ = config_manager.enable_proxy_config(8787, True)
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["model_provider"] == "openai-idfix"
    assert doc["model_providers"]["openai-idfix"]["base_url"] == "http://127.0.0.1:8787/v1"
    assert doc["model_providers"]["openai-idfix"]["supports_websockets"] is True
    assert doc["projects"]["demo"]["trust_level"] == "trusted"

    ok, _ = config_manager.disable_proxy_config()
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert doc["model_provider"] == "custom"


def test_proxy_config_status_tracks_active_port(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    config.write_text('model_provider = "openai"\n', encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config)
    monkeypatch.setattr(config_manager, "STATE_PATH", state)
    assert config_manager.get_proxy_config_status(8787)["active_for_port"] is False
    ok, _ = config_manager.enable_proxy_config(8787, True)
    assert ok
    status = config_manager.get_proxy_config_status(8787)
    assert status["active"] is True
    assert status["active_for_port"] is True
    assert status["base_url"] == "http://127.0.0.1:8787/v1"
    assert config_manager.get_proxy_config_status(9999)["active_for_port"] is False


def test_disable_refuses_external_provider_change(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    config.write_text('model_provider = "openai"\n', encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config)
    monkeypatch.setattr(config_manager, "STATE_PATH", state)
    ok, _ = config_manager.enable_proxy_config(8787, True)
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    doc["model_provider"] = "openai"
    config.write_text(tomlkit.dumps(doc), encoding="utf-8")
    ok, msg = config_manager.disable_proxy_config()
    assert ok is False
    assert "外部修改" in msg
    assert tomlkit.parse(config.read_text(encoding="utf-8"))["model_provider"] == "openai"


def test_disable_refuses_external_idfix_url_change(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.toml"
    state = tmp_path / "state.json"
    config.write_text('model_provider = "openai"\n', encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_PATH", config)
    monkeypatch.setattr(config_manager, "STATE_PATH", state)
    ok, _ = config_manager.enable_proxy_config(8787, True)
    assert ok
    doc = tomlkit.parse(config.read_text(encoding="utf-8"))
    doc["model_providers"]["openai-idfix"]["base_url"] = "http://127.0.0.1:9999/v1"
    config.write_text(tomlkit.dumps(doc), encoding="utf-8")
    ok, msg = config_manager.disable_proxy_config()
    assert ok is False
    assert "base_url" in msg
