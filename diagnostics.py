"""
diagnostics.py — Health and diagnostics utilities for Codex Toolkit.
"""

import os
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

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

def get_diagnostics(port: int) -> dict:
    diag = {
        "codex": {
            "running": False,
            "config_exists": CONFIG_PATH.exists(),
        },
        "proxy": {
            "running": False,
            "port": port,
        },
        "network": {
            "http_proxy": mask_url(os.environ.get("HTTP_PROXY")),
            "https_proxy": mask_url(os.environ.get("HTTPS_PROXY")),
            "all_proxy": mask_url(os.environ.get("ALL_PROXY")),
            "no_proxy": os.environ.get("NO_PROXY")
        }
    }
    
    # Check Codex. tasklist can return the process name with different casing,
    # so always normalise stdout before matching.
    import subprocess
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Codex.exe", "/NH"],
            capture_output=True, text=True, timeout=2
        )
        if "codex.exe" in res.stdout.lower():
            diag["codex"]["running"] = True
    except Exception:
        pass
        
    # Check local proxy health
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health/details")
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                import json
                diag["proxy"]["running"] = True
                diag["proxy"]["details"] = json.loads(response.read().decode("utf-8"))
    except Exception:
        pass

    return diag

def is_port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0
