from types import SimpleNamespace

import process_utils


def test_packaged_proxy_name_detection_is_case_insensitive():
    assert process_utils.is_packaged_toolkit_proxy("CodexBridgeProxy")
    assert process_utils.is_packaged_toolkit_proxy("codexbridgeproxy.exe")
    assert not process_utils.is_packaged_toolkit_proxy("python")


def test_terminate_process_tree_uses_taskkill_tree_flag(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="SUCCESS")

    monkeypatch.setattr(process_utils.subprocess, "run", fake_run)
    monkeypatch.setattr(process_utils, "get_process_name", lambda pid: None)
    assert process_utils.terminate_process_tree(1234) is True
    assert calls[0] == ["taskkill", "/PID", "1234", "/T", "/F"]


def test_wait_for_port_free(monkeypatch):
    states = iter([4321, None])
    monkeypatch.setattr(process_utils, "get_listening_pid", lambda port: next(states))
    monkeypatch.setattr(process_utils.time, "sleep", lambda seconds: None)
    assert process_utils.wait_for_port_free(8787, timeout=1) is True
