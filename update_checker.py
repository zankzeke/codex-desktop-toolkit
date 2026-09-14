"""Conservative GitHub release update checker.

This module only checks metadata and returns a release URL.  It never downloads
or installs updates automatically.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

UPDATE_STATE = Path.home() / ".codex-toolkit" / "update.json"
LATEST_RELEASE_API = "https://api.github.com/repos/zankzeke/codex-desktop-toolkit/releases/latest"
RELEASES_URL = "https://github.com/zankzeke/codex-desktop-toolkit/releases"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60


def _version_tuple(value: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", value or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = list(_version_tuple(latest)), list(_version_tuple(current))
    width = max(len(a), len(b))
    a += [0] * (width - len(a))
    b += [0] * (width - len(b))
    return tuple(a) > tuple(b)


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(data: dict[str, Any]) -> None:
    try:
        UPDATE_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = UPDATE_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(UPDATE_STATE)
    except Exception:
        pass


def should_check_now() -> bool:
    state = _load_state()
    last = float(state.get("last_check", 0) or 0)
    return time.time() - last >= CHECK_INTERVAL_SECONDS


def check_for_update(current_version: str, *, force: bool = False, timeout: float = 4.0) -> dict[str, Any]:
    if not force and not should_check_now():
        return {"checked": False, "reason": "interval", "update_available": False}

    result: dict[str, Any] = {
        "checked": True,
        "update_available": False,
        "current_version": current_version,
        "latest_version": None,
        "release_url": RELEASES_URL,
    }
    try:
        req = urllib.request.Request(
            LATEST_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"CodexBridgeToolkit/{current_version}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        tag = str(payload.get("tag_name") or "").lstrip("vV")
        result["latest_version"] = tag or None
        result["release_url"] = payload.get("html_url") or RELEASES_URL
        result["update_available"] = bool(tag and is_newer(tag, current_version))
    except Exception as exc:
        result["error"] = type(exc).__name__
    finally:
        _save_state({"last_check": time.time(), "latest_version": result.get("latest_version")})
    return result
