"""Auto-attach behavior: pre-arm on opt-in, observe CLI/Desktop launches safely."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from codex_toolkit_gui import App, ProxyTab
from startup_manager import AutomationSettings, load_automation_settings, save_automation_settings


def _fake_proxy(process=None):
    obj = SimpleNamespace()
    obj._app = SimpleNamespace(_closing=False, _proxy_proc=process)
    obj._port_var = SimpleNamespace(get=lambda: "8787")
    obj._ws_var = SimpleNamespace(get=lambda: True)
    obj._append_log = Mock()
    obj._start_proxy = Mock()
    return obj


def test_auto_attach_is_opt_in_and_persistent(tmp_path):
    with patch("startup_manager.STATE_FILE", tmp_path / "state.json"):
        assert load_automation_settings().auto_attach_codex is False
        save_automation_settings(AutomationSettings(auto_attach_codex=True))
        assert load_automation_settings().auto_attach_codex is True


def test_prearm_starts_proxy_when_not_running():
    obj = _fake_proxy()
    with patch("codex_toolkit_gui.load_automation_settings", return_value=AutomationSettings(auto_attach_codex=True)):
        ProxyTab._arm_auto_attach(obj)
    obj._start_proxy.assert_called_once()


def test_auto_attach_is_noop_when_disabled():
    obj = _fake_proxy()
    with patch("codex_toolkit_gui.load_automation_settings", return_value=AutomationSettings()):
        ProxyTab._arm_auto_attach(obj, codex_detected=True)
    obj._start_proxy.assert_not_called()


def test_active_proxy_does_not_rewrite_config():
    obj = _fake_proxy(SimpleNamespace(poll=lambda: None))
    response = Mock()
    response.status = 200
    with patch("codex_toolkit_gui.load_automation_settings", return_value=AutomationSettings(auto_attach_codex=True)), \
         patch("codex_toolkit_gui.urllib.request.urlopen") as urlopen, \
         patch("codex_toolkit_gui.get_proxy_config_status", return_value={"active_for_port": True}), \
         patch("codex_toolkit_gui.enable_proxy_config") as enable:
        urlopen.return_value.__enter__.return_value = response
        ProxyTab._arm_auto_attach(obj, codex_detected=True)
    enable.assert_not_called()
    obj._start_proxy.assert_not_called()


def test_auto_attach_injects_without_terminating_running_cli():
    obj = _fake_proxy(SimpleNamespace(poll=lambda: None))
    response = Mock()
    response.status = 200
    with patch("codex_toolkit_gui.load_automation_settings", return_value=AutomationSettings(auto_attach_codex=True)), \
         patch("codex_toolkit_gui.urllib.request.urlopen") as urlopen, \
         patch("codex_toolkit_gui.get_proxy_config_status", return_value={"active_for_port": False, "active": False}), \
         patch("codex_toolkit_gui.enable_proxy_config", return_value=(True, "OK")) as enable, \
         patch("codex_toolkit_gui.restart_codex") as restart:
        urlopen.return_value.__enter__.return_value = response
        ProxyTab._arm_auto_attach(obj, codex_detected=True)
    enable.assert_called_once_with(8787, True)
    restart.assert_not_called()
    assert "可能需要重启" in obj._append_log.call_args_list[-1].args[0]


def test_external_managed_base_change_is_not_overwritten():
    obj = _fake_proxy(SimpleNamespace(poll=lambda: None))
    response = Mock()
    response.status = 200
    with patch("codex_toolkit_gui.load_automation_settings", return_value=AutomationSettings(auto_attach_codex=True)), \
         patch("codex_toolkit_gui.urllib.request.urlopen") as urlopen, \
         patch("codex_toolkit_gui.get_proxy_config_status", return_value={
             "active_for_port": False, "active": True, "managed_snapshot_matches": False,
         }), \
         patch("codex_toolkit_gui.enable_proxy_config") as enable:
        urlopen.return_value.__enter__.return_value = response
        ProxyTab._arm_auto_attach(obj, codex_detected=True)
    enable.assert_not_called()


def test_process_monitor_triggers_only_on_new_launch():
    proxy = SimpleNamespace(_arm_auto_attach=Mock())
    obj = SimpleNamespace(
        _closing=False, _auto_attach_poll_busy=True,
        _auto_attach_was_running=False, _proxy_tab=proxy,
    )
    with patch("codex_toolkit_gui.load_automation_settings", return_value=AutomationSettings(auto_attach_codex=True)):
        App._on_codex_presence(obj, True)
        App._on_codex_presence(obj, True)
        App._on_codex_presence(obj, False)
        App._on_codex_presence(obj, True)
    assert proxy._arm_auto_attach.call_count == 2
    assert all(call.kwargs == {"codex_detected": True} for call in proxy._arm_auto_attach.call_args_list)


def test_monitor_resets_when_opted_out():
    proxy = SimpleNamespace(_arm_auto_attach=Mock())
    obj = SimpleNamespace(
        _closing=False, _auto_attach_poll_busy=True,
        _auto_attach_was_running=True, _proxy_tab=proxy,
    )
    with patch("codex_toolkit_gui.load_automation_settings", return_value=AutomationSettings()):
        App._on_codex_presence(obj, True)
    assert obj._auto_attach_was_running is False
    proxy._arm_auto_attach.assert_not_called()
