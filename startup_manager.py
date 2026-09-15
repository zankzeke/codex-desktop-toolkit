"""startup_manager.py — Windows login auto-start, notification rate-limiting, and automation settings.

Controls per-user HKCU Startup registry registration, notification rate-limiting,
and persistent preferences for one-click and background automation.
"""
from __future__ import annotations

import json
import os
import sys
import time
import winreg
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STATE_FILE = Path.home() / ".codex-toolkit" / "state.json"
REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_APP_NAME = "CodexBridgeToolkit"


@dataclass
class AutomationSettings:
    launch_on_startup: bool = False
    auto_start_proxy: bool = False
    auto_apply_provider: bool = False
    auto_stop_proxy_on_exit: bool = False
    windows_notifications: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_automation_settings() -> AutomationSettings:
    """Load user automation settings from local state, defaulting all to False."""
    if not STATE_FILE.exists():
        return AutomationSettings()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        cfg = data.get("automation") or {}
        return AutomationSettings(
            launch_on_startup=bool(cfg.get("launch_on_startup", False)),
            auto_start_proxy=bool(cfg.get("auto_start_proxy", False)),
            auto_apply_provider=bool(cfg.get("auto_apply_provider", False)),
            auto_stop_proxy_on_exit=bool(cfg.get("auto_stop_proxy_on_exit", False)),
            windows_notifications=bool(cfg.get("windows_notifications", False)),
        )
    except Exception:
        return AutomationSettings()


def save_automation_settings(settings: AutomationSettings) -> None:
    """Save automation preferences into local state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["automation"] = settings.to_dict()

    temp_path = STATE_FILE.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, STATE_FILE)


class WindowsStartupManager:
    """Manage HKCU Run key for per-user startup without requiring Administrator privileges."""

    def __init__(self, key_name: str = REG_APP_NAME) -> None:
        self.key_name = key_name

    def is_startup_enabled(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, self.key_name)
                return bool(val)
        except (FileNotFoundError, OSError):
            return False

    def enable_startup(self, custom_command: str | None = None) -> bool:
        if sys.platform != "win32":
            return False
        try:
            if custom_command:
                cmd = custom_command
            elif getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                script = Path(__file__).parent / "codex_toolkit_gui.py"
                cmd = f'"{sys.executable}" "{script.resolve()}"'

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self.key_name, 0, winreg.REG_SZ, cmd)
            return True
        except OSError:
            return False

    def disable_startup(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.key_name)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


class NotificationManager:
    """Rate-limited notification dispatcher for user alerts."""

    def __init__(self, default_cooldown: float = 60.0) -> None:
        self.default_cooldown = default_cooldown
        self._last_sent: dict[str, float] = {}

    def should_notify(self, key: str, cooldown: float | None = None) -> bool:
        cd = self.default_cooldown if cooldown is None else cooldown
        last = self._last_sent.get(key, 0.0)
        return (time.time() - last) >= cd

    def record_notified(self, key: str) -> None:
        self._last_sent[key] = time.time()

    def send_notification(self, title: str, message: str, key: str | None = None, cooldown: float = 60.0) -> bool:
        """Send a non-blocking Windows notification if rate limit allows."""
        dedup_key = key or f"{title}:{message[:40]}"
        if not self.should_notify(dedup_key, cooldown):
            return False

        self.record_notified(dedup_key)

        # Dispatch via PowerShell notification if on Windows
        if sys.platform == "win32":
            import subprocess
            ps_script = (
                f'[void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); '
                f'$obj = New-Object System.Windows.Forms.NotifyIcon; '
                f'$obj.Icon = [System.Drawing.SystemIcons]::Information; '
                f'$obj.BalloonTipTitle = "{title}"; '
                f'$obj.BalloonTipText = "{message}"; '
                f'$obj.Visible = $True; '
                f'$obj.ShowBalloonTip(3000);'
            )
            try:
                subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_script],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        return True


GLOBAL_STARTUP_MANAGER = WindowsStartupManager()
GLOBAL_NOTIFIER = NotificationManager()
