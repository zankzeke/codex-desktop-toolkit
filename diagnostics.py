"""Health and diagnostics utilities for Codex Bridge Toolkit."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from config_manager import get_proxy_config_status
from process_utils import get_port_owner

CODEX_HOME = Path.home() / ".codex"
CONFIG_PATH = CODEX_HOME / "config.toml"


def mask_url(url: str | None) -> str | None:
    """Return a diagnostics-safe URL without credentials, query, or fragment."""
    if not url:
        return url
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        if parsed.username is not None or parsed.password is not None:
            host = f"***:***@{host}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except Exception:
        return "<redacted-url>"


def _fetch_json(url: str, timeout: float = 2.0) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CodexBridgeToolkit/diagnostics"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return None


def get_codex_process_info() -> dict[str, Any]:
    result: dict[str, Any] = {"running": False, "path": None, "version": None}
    try:
        ps = (
            "$p = Get-Process codex -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if ($p) { $v = (Get-Item $p.Path).VersionInfo.FileVersion; "
            "Write-Output ($p.Path); Write-Output ($v) }"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=4,
        )
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        # PowerShell itself (or a mocked subprocess in tests) can emit diagnostic
        # text on stdout. Only a real codex.exe-looking path is authoritative.
        if lines and lines[0].lower().replace("/", "\\").endswith("\\codex.exe"):
            result["running"] = True
            result["path"] = lines[0]
            if len(lines) > 1:
                result["version"] = lines[1]
            return result
    except Exception:
        pass

    # Fallback to tasklist. Windows output casing is not stable.
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Codex.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        result["running"] = "codex.exe" in res.stdout.lower()
    except Exception:
        pass
    return result


def get_diagnostics(port: int) -> dict[str, Any]:
    owner_pid, owner_name = get_port_owner(port)
    diag: dict[str, Any] = {
        "codex": {
            **get_codex_process_info(),
            "config_exists": CONFIG_PATH.exists(),
            "config": get_proxy_config_status(port),
        },
        "proxy": {
            "running": False,
            "port": port,
            "owner_pid": owner_pid,
            "owner_name": owner_name,
        },
        "network": {
            "http_proxy": mask_url(os.environ.get("HTTP_PROXY")),
            "https_proxy": mask_url(os.environ.get("HTTPS_PROXY")),
            "all_proxy": mask_url(os.environ.get("ALL_PROXY")),
            "no_proxy": os.environ.get("NO_PROXY"),
        },
    }

    details = _fetch_json(f"http://127.0.0.1:{port}/health/details")
    stats = _fetch_json(f"http://127.0.0.1:{port}/stats")
    if details is not None:
        diag["proxy"]["running"] = True
        diag["proxy"]["details"] = details
    if stats is not None:
        diag["proxy"]["stats"] = stats
    return diag


collect_diagnostics = get_diagnostics


def build_diagnostic_report(data: dict[str, Any], app_version: str) -> str:
    """Create a Markdown report containing only privacy-safe diagnostics."""
    codex = data.get("codex", {})
    cfg = codex.get("config", {}) or {}
    proxy = data.get("proxy", {})
    details = proxy.get("details", {}) or {}
    stats = proxy.get("stats", {}) or {}
    ws = stats.get("websocket", {}) or {}
    err = stats.get("last_error") or {}
    net = data.get("network", {})

    codex_path = str(codex.get("path") or "unknown")
    try:
        from pathlib import Path
        home = str(Path.home())
        if home and codex_path.lower().startswith(home.lower()):
            codex_path = "~" + codex_path[len(home):]
    except Exception:
        pass

    lines = [
        "# Codex Bridge Toolkit diagnostic report",
        "",
        f"- Toolkit version: {app_version}",
        f"- Codex running: {bool(codex.get('running'))}",
        f"- Codex version: {codex.get('version') or 'unknown'}",
        f"- Codex executable: {codex_path}",
        f"- Config exists: {bool(codex.get('config_exists'))}",
        f"- model_provider: {cfg.get('provider') or 'unknown'}",
        f"- Local provider active for port: {bool(cfg.get('active_for_port'))}",
        f"- Proxy running: {bool(proxy.get('running'))}",
        f"- Proxy port: {proxy.get('port')}",
        f"- Port owner: {proxy.get('owner_name') or 'unknown'} (PID {proxy.get('owner_pid') or 'unknown'})",
        f"- Upstream: {details.get('upstream') or 'unknown'}",
        f"- Outbound proxy mode: {details.get('proxy_mode') or 'unknown'}",
        f"- Explicit upstream proxy: {details.get('upstream_proxy') or 'none'}",
        f"- Traffic verified: {bool(stats.get('traffic_verified'))}",
        f"- Requests: {stats.get('requests_total', 0)}",
        f"- Last transport: {stats.get('last_transport') or 'none'}",
        f"- Last request: {stats.get('last_request_method') or '-'} {stats.get('last_request_path') or '-'}",
        f"- Last response status: {stats.get('last_response_status') or 'none'}",
        f"- ID fixes total: {stats.get('id_fixes_total', 0)}",
        f"- Reasoning drops total: {stats.get('reasoning_drops_total', 0)}",
        f"- WS active: {ws.get('active', 0)}",
        f"- WS handshakes: {ws.get('handshakes', 0)}",
        f"- WS reconnects: {ws.get('reconnects', 0)}",
        f"- WS failures: {ws.get('failures', 0)}",
        f"- WS last close code: {ws.get('last_close_code') if ws.get('last_close_code') is not None else 'none'}",
        f"- Last error category: {err.get('category') or 'none'}",
        f"- Last error title: {err.get('title') or 'none'}",
        "",
        "## Environment proxy variables (masked)",
        f"- HTTP_PROXY: {net.get('http_proxy') or 'unset'}",
        f"- HTTPS_PROXY: {net.get('https_proxy') or 'unset'}",
        f"- ALL_PROXY: {net.get('all_proxy') or 'unset'}",
        f"- NO_PROXY: {'set (contents redacted)' if net.get('no_proxy') else 'unset'}",
        "",
        "> Authentication headers, cookies, request bodies, response bodies and URL query strings are intentionally omitted.",
    ]
    return "\n".join(lines)


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0
