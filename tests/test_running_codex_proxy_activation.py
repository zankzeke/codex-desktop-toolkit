from pathlib import Path


def test_running_codex_activation_path_exists():
    text = Path("codex_toolkit_gui.py").read_text(encoding="utf-8")
    assert "def _on_proxy_ready" in text
    assert "get_proxy_config_status(port_num)" in text
    assert "enable_proxy_config(port_num, self._ws_var.get())" in text
    assert "运行中的 Codex 不会可靠地热切换 provider" in text
