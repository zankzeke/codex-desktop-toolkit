from pathlib import Path


def test_gui_closes_full_proxy_tree_and_handles_stale_proxy():
    text = Path("codex_toolkit_gui.py").read_text(encoding="utf-8")
    assert "self._proxy_tab._stop_proxy(for_exit=True)" in text
    assert "terminate_process_tree(pid)" in text
    assert "is_packaged_toolkit_proxy(owner_name)" in text
    assert "wait_for_port_free(port_num)" in text
    assert "如何获取科学上网地址" in text
