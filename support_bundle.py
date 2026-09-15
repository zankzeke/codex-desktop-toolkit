"""Create a privacy-safe support bundle for issue triage."""
from __future__ import annotations

import json
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from config_manager import CONFIG_PATH
from diagnostics import build_diagnostic_report, get_diagnostics, mask_url

_SENSITIVE_KEY = re.compile(
    r"(?:token|secret|password|passwd|cookie|authorization|credential|api[_-]?key|refresh[_-]?token)",
    re.I,
)
_URL = re.compile(r"https?://[^\s\"']+", re.I)
_AUTH_LINE = re.compile(r"(?i)(authorization|cookie|set-cookie|x-access-token|x-refresh-token)\s*[:=].*")


def _home_redact(text: str) -> str:
    try:
        home = str(Path.home())
        if home:
            return text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    except Exception:
        pass
    return text


def _sanitize_string(value: str) -> str:
    value = _home_redact(value)
    return _URL.sub(lambda m: mask_url(m.group(0)) or "<redacted-url>", value)


def _sanitize_obj(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key) and isinstance(value, (str, bytes)):
        return "<redacted>"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() == "no_proxy":
                out[k] = "<set; contents redacted>" if v else None
            else:
                out[k] = _sanitize_obj(v, str(k))
        return out
    if isinstance(value, list):
        return [_sanitize_obj(v, key) for v in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


def redact_config_text(text: str) -> str:
    """Redact likely credentials and URL user-info while keeping config shape."""
    out: list[str] = []
    for raw in text.splitlines():
        line = _home_redact(raw)
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+)(\s*=\s*)(.*)$", line)
        if match:
            indent, key, sep, value = match.groups()
            if _SENSITIVE_KEY.search(key) and value.strip().startswith(("\"", "'")):
                out.append(f'{indent}{key}{sep}"<redacted>"')
                continue
        line = _URL.sub(lambda m: mask_url(m.group(0)) or "<redacted-url>", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def redact_log_text(text: str, max_lines: int = 300) -> str:
    lines = text.splitlines()[-max_lines:]
    safe: list[str] = []
    for line in lines:
        if _AUTH_LINE.search(line):
            key = _AUTH_LINE.search(line).group(1)  # type: ignore[union-attr]
            safe.append(f"{key}: <redacted>")
            continue
        safe.append(_sanitize_string(line))
    return "\n".join(safe) + ("\n" if safe else "")


def create_support_bundle(
    *,
    port: int,
    app_version: str,
    output_path: str | Path | None = None,
    recent_log_text: str = "",
) -> Path:
    """Create a ZIP without credentials, message bodies or query strings."""
    data = get_diagnostics(int(port))
    safe_data = _sanitize_obj(data)
    report = build_diagnostic_report(data, app_version)

    if output_path is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output = Path(tempfile.gettempdir()) / f"CodexBridge-support-{stamp}.zip"
    else:
        output = Path(output_path)
        if output.suffix.lower() != ".zip":
            output = output.with_suffix(".zip")
    output.parent.mkdir(parents=True, exist_ok=True)

    config_text = "# config.toml not found\n"
    if CONFIG_PATH.exists():
        try:
            config_text = redact_config_text(CONFIG_PATH.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            config_text = f"# failed to read config.toml: {type(exc).__name__}\n"

    proxy_stats = (safe_data.get("proxy") or {}).get("stats") or {}
    last_error = proxy_stats.get("last_error") or {}
    recent_errors = json.dumps(last_error, ensure_ascii=False, indent=2) if last_error else "No classified runtime error.\n"
    if recent_log_text:
        recent_errors += "\n\n--- recent proxy log (redacted) ---\n" + redact_log_text(recent_log_text)

    codex = safe_data.get("codex") or {}
    codex_version = (
        f"Toolkit: {app_version}\n"
        f"Codex running: {bool(codex.get('running'))}\n"
        f"Codex version: {codex.get('version') or 'unknown'}\n"
        f"Codex executable: {codex.get('path') or 'unknown'}\n"
    )

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.md", _home_redact(report))
        zf.writestr("environment.json", json.dumps(safe_data, ensure_ascii=False, indent=2))
        zf.writestr("proxy-stats.json", json.dumps(proxy_stats, ensure_ascii=False, indent=2))
        zf.writestr("codex-version.txt", codex_version)
        zf.writestr("config-redacted.toml", config_text)
        zf.writestr("recent-errors.txt", recent_errors)
        zf.writestr(
            "README.txt",
            "This bundle is generated for Codex Bridge Toolkit support.\n"
            "Authorization/Cookie values, URL query strings and known secret fields are redacted.\n"
            "Chat/request/response bodies are not included. Review the archive before posting publicly.\n",
        )
    return output
