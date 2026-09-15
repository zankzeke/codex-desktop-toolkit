from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from startup_manager import (
    AutomationSettings,
    NotificationManager,
    WindowsStartupManager,
    load_automation_settings,
    save_automation_settings,
)


def test_automation_settings_persistence(tmp_path: Path):
    with patch("startup_manager.STATE_FILE", tmp_path / "state.json"):
        default_cfg = load_automation_settings()
        assert default_cfg.launch_on_startup is False
        assert default_cfg.auto_start_proxy is False

        new_cfg = AutomationSettings(
            launch_on_startup=True,
            auto_start_proxy=True,
            auto_apply_provider=True,
            auto_stop_proxy_on_exit=True,
            windows_notifications=True,
        )
        save_automation_settings(new_cfg)

        loaded = load_automation_settings()
        assert loaded.launch_on_startup is True
        assert loaded.auto_start_proxy is True
        assert loaded.windows_notifications is True


def test_notification_manager_rate_limiting():
    nm = NotificationManager(default_cooldown=0.5)
    key = "test_event"

    assert nm.should_notify(key) is True
    nm.record_notified(key)
    assert nm.should_notify(key) is False

    # Wait for cooldown to pass
    time.sleep(0.55)
    assert nm.should_notify(key) is True


def test_startup_manager_mocked_registry():
    mgr = WindowsStartupManager("TestCodexApp")
    fake_registry = {}

    def fake_set_value(key, name, reserved, reg_type, value):
        fake_registry[name] = value

    def fake_query_value(key, name):
        if name in fake_registry:
            return fake_registry[name], 1
        raise FileNotFoundError()

    def fake_delete_value(key, name):
        fake_registry.pop(name, None)

    mock_key = MagicMock()
    with patch("winreg.OpenKey", return_value=mock_key):
        with patch("winreg.SetValueEx", side_effect=fake_set_value):
            with patch("winreg.QueryValueEx", side_effect=fake_query_value):
                with patch("winreg.DeleteValue", side_effect=fake_delete_value):
                    with patch("sys.platform", "win32"):
                        assert mgr.is_startup_enabled() is False
                        assert mgr.enable_startup(custom_command='"C:\\Fake\\Toolkit.exe"') is True
                        assert mgr.is_startup_enabled() is True
                        assert mgr.disable_startup() is True
                        assert mgr.is_startup_enabled() is False
