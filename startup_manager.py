"""Windows login-startup helpers for Codex Bridge Toolkit."""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CodexBridgeToolkit"


def _command_for_executable(executable: str | Path | None = None) -> str:
    if executable is None:
        executable = sys.executable
    path = str(Path(executable).resolve())
    return f'"{path}" --background'


def is_startup_supported() -> bool:
    return os.name == "nt"


def get_startup_command() -> str | None:
    if not is_startup_supported():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except OSError:
        return None


def is_startup_enabled() -> bool:
    return bool(get_startup_command())


def set_startup_enabled(enabled: bool, executable: str | Path | None = None) -> tuple[bool, str]:
    if not is_startup_supported():
        return False, "开机启动仅支持 Windows。"
    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if enabled:
                command = _command_for_executable(executable)
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
                return True, "已启用 Windows 登录后自动启动 Toolkit。"
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
            return True, "已关闭 Windows 登录后自动启动 Toolkit。"
    except OSError as exc:
        return False, f"无法修改 Windows 启动项：{exc}"
