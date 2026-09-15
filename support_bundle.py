"""support_bundle.py — One-click sanitized diagnostics bundle generator.

Gathers diagnostics, environment, proxy stats, and configuration into a ZIP file.
Strictly redacts usernames, file paths, credentials, tokens, cookies, queries,
and conversation content.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from config_manager import CONFIG_PATH
from diagnostics import collect_diagnostics, build_diagnostic_report
from version import APP_VERSION


def redact_user_paths(text: str) -> str:
    """Replace user home directory occurrences with '~'."""
    if not text:
        return text
    try:
        home = str(Path.home())
        home_win = home.replace("/", "\\")
        home_posix = home.replace("\\", "/")
        text = re.sub(re.escape(home_win), "~", text, flags=re.IGNORECASE)
        text = re.sub(re.escape(home_posix), "~", text, flags=re.IGNORECASE)
    except Exception:
        pass
    return text


def redact_url_sensitive(url: str | None) -> str:
    """Strip authentication credentials, query strings, and fragments from a URL."""
    if not url:
        return ""
    try:
        parsed = urlsplit(str(url).strip())
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        # Discard username, password, query params, fragment
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except Exception:
        return "<redacted-url>"


def redact_text_content(content: str) -> str:
    """Apply holistic sanitization across any arbitrary text before saving."""
    if not content:
        return ""

    # 1. Redact home paths
    text = redact_user_paths(content)

    # 2. Redact common API/token formats, including JWT-like values.
    text = re.sub(r"\bsk-[a-zA-Z0-9_-]{10,}\b", "sk-***REDACTED***", text)
    text = re.sub(r"\b(?:ghp_|github_pat_)[a-zA-Z0-9_-]{10,}\b", "***REDACTED***", text)
    text = re.sub(
        r"\b[a-zA-Z0-9_-]{12,}\.[a-zA-Z0-9_-]{12,}\.[a-zA-Z0-9_-]{12,}\b",
        "***REDACTED-JWT***",
        text,
    )

    # 3. Redact authentication headers regardless of auth scheme.
    text = re.sub(
        r"(?im)^((?:proxy-)?authorization\s*[:=]\s*)[^\r\n]+",
        r"\1***REDACTED***",
        text,
    )
    text = re.sub(r"(?i)(bearer\s+)[^\s,;\"]+", r"\1***REDACTED***", text)

    # 4. Redact cookie/token header and key-value forms.
    text = re.sub(r"(?im)^((?:set-)?cookie\s*[:=]\s*)[^\r\n]+", r"\1***REDACTED***", text)
    text = re.sub(
        r"(?i)((?:session[_-]?token|access[_-]?token|refresh[_-]?token|x-access-token|x-refresh-token)\s*[:=]\s*)[^\s,;\"]+",
        r"\1***REDACTED***",
        text,
    )

    # 5. Redact URLs with embedded user:pass or queries
    def url_cleaner(match: re.Match) -> str:
        raw_url = match.group(0)
        return redact_url_sensitive(raw_url)

    text = re.sub(r"https?://[^\s\"'<>()]+", url_cleaner, text)
    return text


def generate_redacted_config() -> str:
    """Load config.toml and strip any credentials or secret keys."""
    if not CONFIG_PATH.exists():
        return "# config.toml does not exist\n"

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        # Remove any api_key or token lines
        lines = []
        for line in raw.splitlines():
            s = line.strip()
            if any(k in s.lower() for k in (
                "api_key", "apikey", "secret", "token", "password", "passwd",
                "credential", "cookie", "authorization", "private_key", "access_key",
            )):
                key_part = line.split("=", 1)[0]
                lines.append(f'{key_part}= "***REDACTED***"')
            else:
                lines.append(line)
        cleaned = "\n".join(lines)
        return redact_text_content(cleaned)
    except Exception as exc:
        return f"# Error reading config.toml: {exc}\n"


def generate_environment_info() -> dict[str, Any]:
    """Collect platform, Python, and redacted network proxy settings."""
    no_proxy_set = bool(os.environ.get("NO_PROXY") or os.environ.get("no_proxy"))
    return {
        "toolkit_version": APP_VERSION,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "system": platform.system(),
        "release": platform.release(),
        "executable_path": redact_user_paths(sys.executable),
        "proxies": {
            "http_proxy": redact_url_sensitive(os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")),
            "https_proxy": redact_url_sensitive(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
            "all_proxy": redact_url_sensitive(os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")),
            "no_proxy": "set (contents redacted)" if no_proxy_set else "unset",
        },
    }


def create_support_bundle(
    output_dir: Path | None = None,
    port: int = 8787,
    proxy_stats: dict[str, Any] | None = None,
    recent_errors: list[dict[str, Any]] | None = None,
) -> Path:
    """Create a sanitized ZIP archive containing non-sensitive diagnostic data.

    Returns the absolute path to the generated ZIP file.
    """
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    target_folder = output_dir or (Path.home() / "Desktop" if (Path.home() / "Desktop").exists() else Path.cwd())
    bundle_name = f"CodexBridgeSupport-{timestamp}.zip"
    zip_path = target_folder / bundle_name

    # 1. Diagnostics report
    diag_data = collect_diagnostics(port)
    diag_md = build_diagnostic_report(diag_data, APP_VERSION)
    diag_md_cleaned = redact_text_content(diag_md)

    # 2. Environment JSON
    env_info = generate_environment_info()
    env_json_str = json.dumps(env_info, indent=2, ensure_ascii=False)

    # 3. Proxy stats JSON
    stats_data = proxy_stats or diag_data.get("proxy", {}).get("stats") or {}
    stats_json_str = redact_text_content(json.dumps(stats_data, indent=2, ensure_ascii=False))

    # 4. Codex version info
    codex_data = diag_data.get("codex", {})
    codex_version_str = (
        f"Codex Version: {codex_data.get('version') or 'unknown'}\n"
        f"Running: {bool(codex_data.get('running'))}\n"
        f"Executable: {redact_user_paths(str(codex_data.get('path') or 'unknown'))}\n"
    )

    # 5. Redacted config.toml
    config_str = generate_redacted_config()

    # 6. Recent errors
    errors_list = recent_errors or []
    errors_str = redact_text_content(json.dumps(errors_list, indent=2, ensure_ascii=False))

    # Create ZIP archive
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.md", diag_md_cleaned)
        zf.writestr("environment.json", env_json_str)
        zf.writestr("proxy-stats.json", stats_json_str)
        zf.writestr("codex-version.txt", codex_version_str)
        zf.writestr("config-redacted.toml", config_str)
        zf.writestr("recent-errors.txt", errors_str)

    return zip_path
