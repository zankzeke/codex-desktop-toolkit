"""Windows process helpers used by the GUI proxy lifecycle.

The packaged proxy is a PyInstaller one-file executable. On Windows the
bootloader may have a child process, so stopping only the Popen parent can
leave the actual proxy alive and keep port 8787 occupied. These helpers stop
the full process tree and can identify a stale packaged proxy by its listening
port.
"""
from __future__ import annotations

import subprocess
import time
from typing import Optional, Tuple


def get_listening_pid(port: int) -> Optional[int]:
    """Return the PID listening on *port*, or ``None`` when unavailable."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$c = Get-NetTCPConnection -State Listen -LocalPort {int(port)} "
                    "-ErrorAction SilentlyContinue | Select-Object -First 1; "
                    "if ($c) { $c.OwningProcess }"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
        value = result.stdout.strip()
        return int(value) if value.isdigit() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def get_process_name(pid: int) -> Optional[str]:
    """Return a Windows process name without the .exe suffix."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue).ProcessName",
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
        name = result.stdout.strip()
        return name or None
    except (OSError, subprocess.SubprocessError):
        return None


def get_port_owner(port: int) -> Tuple[Optional[int], Optional[str]]:
    pid = get_listening_pid(port)
    if pid is None:
        return None, None
    return pid, get_process_name(pid)


def is_packaged_toolkit_proxy(name: str | None) -> bool:
    """Whether a process name is the packaged Codex Bridge proxy."""
    if not name:
        return False
    normalized = name.lower().removesuffix(".exe")
    return normalized == "codexbridgeproxy"


def terminate_process_tree(pid: int, timeout: float = 4.0) -> bool:
    """Force-stop a Windows process tree and wait briefly for it to disappear."""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=max(2.0, timeout),
        )
        if result.returncode not in (0, 128):
            return False
    except (OSError, subprocess.SubprocessError):
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_process_name(pid) is None:
            return True
        time.sleep(0.1)
    return get_process_name(pid) is None


def wait_for_port_free(port: int, timeout: float = 4.0) -> bool:
    """Wait until no TCP listener owns *port*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_listening_pid(port) is None:
            return True
        time.sleep(0.1)
    return get_listening_pid(port) is None
