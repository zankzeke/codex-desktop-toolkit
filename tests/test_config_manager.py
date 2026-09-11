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
