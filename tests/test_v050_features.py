from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from codex_toolkit_gui import App, run_gui_smoke_test
from config_manager import (
    get_transport_mode,
    set_transport_mode,
    get_circuit_breaker_config,
    save_circuit_breaker_config,
)
from transport_policy import GLOBAL_CIRCUIT_BREAKER, CircuitState



def test_v050_transport_mode_and_circuit_breaker_persistence(tmp_path: Path, monkeypatch):
    fake_config = tmp_path / "config.toml"
    fake_state = tmp_path / "state.json"
    fake_config.write_text('[model_providers.custom]\n', encoding="utf-8")
    monkeypatch.setattr("config_manager.CONFIG_PATH", fake_config)
    monkeypatch.setattr("config_manager.STATE_PATH", fake_state)

    assert get_transport_mode() == "auto"
    set_transport_mode("http")
    assert get_transport_mode() == "http"
    set_transport_mode("websocket")
    assert get_transport_mode() == "websocket"

    save_circuit_breaker_config(failure_threshold=5, cooldown_seconds=600)
    cb_cfg = get_circuit_breaker_config()
    assert cb_cfg["failure_threshold"] == 5
    assert cb_cfg["cooldown_seconds"] == 600


import subprocess
import sys


def test_v050_gui_smoke_subprocess():
    """Verify that run_gui_smoke_test runs cleanly and all 7 tabs are constructed in an isolated process."""
    res = subprocess.run(
        [sys.executable, "codex_toolkit_gui.py", "--smoke-test"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Smoke test failed: stdout={res.stdout}, stderr={res.stderr}"
