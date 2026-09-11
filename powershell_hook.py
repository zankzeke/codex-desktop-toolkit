"""
powershell_hook.py — Safe injection of AGY proxy wrapper with env restoration.
"""

import re
from pathlib import Path
from typing import Tuple
from history_fixer import backup_file

def get_profile_path() -> Path:
    doc_path = Path.home() / "Documents"
    ps_dir = doc_path / "PowerShell"
    if not ps_dir.exists():
        ps_dir = doc_path / "WindowsPowerShell"
    return ps_dir / "Microsoft.PowerShell_profile.ps1"

def _validate_proxy_address(address: str) -> None:
    # Strict check: alphanumeric, dot, hyphen, brackets (for IPv6), and colon.
    # We do a basic structural check to prevent injection: no quotes, semicolons, spaces, newlines.
    if not re.fullmatch(r"(?:[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:]+\]):\d+", address):
        raise ValueError(f"Invalid proxy address format: {address}")
    host, port_str = address.rsplit(':', 1)
    if not host:
        raise ValueError(f"Invalid proxy host: {address}")
    if not (1 <= int(port_str) <= 65535):
        raise ValueError(f"Invalid proxy port: {port_str}")

def get_hook_script(proxy_address: str) -> str:
    _validate_proxy_address(proxy_address)
    return f"""
# --- AGY PROXY HOOK START ---
function agy {{
    $oldHttp = [Environment]::GetEnvironmentVariable("HTTP_PROXY", "Process")
    $oldHttps = [Environment]::GetEnvironmentVariable("HTTPS_PROXY", "Process")
    $oldAll = [Environment]::GetEnvironmentVariable("ALL_PROXY", "Process")
    $oldNo = [Environment]::GetEnvironmentVariable("NO_PROXY", "Process")

    try {{
        $env:HTTPS_PROXY = 'http://{proxy_address}'
        $env:HTTP_PROXY  = 'http://{proxy_address}'
        $env:ALL_PROXY   = 'socks5://{proxy_address}'
        Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue
        
        $agy_exe = (Get-Command agy.exe -ErrorAction SilentlyContinue).Path
        if ($agy_exe) {{
            & $agy_exe $args
        }} else {{
            $agy_cmd = (Get-Command agy -CommandType Application,ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1).Path
            if ($agy_cmd) {{ & $agy_cmd $args }} else {{ Write-Error "agy command not found." }}
        }}
    }} finally {{
        if ($null -ne $oldHttp) {{ $env:HTTP_PROXY = $oldHttp }} else {{ Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue }}
        if ($null -ne $oldHttps) {{ $env:HTTPS_PROXY = $oldHttps }} else {{ Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue }}
        if ($null -ne $oldAll) {{ $env:ALL_PROXY = $oldAll }} else {{ Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue }}
        if ($null -ne $oldNo) {{ $env:NO_PROXY = $oldNo }} else {{ Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue }}
    }}
}}
# --- AGY PROXY HOOK END ---
"""

def check_hook_status() -> bool:
    profile = get_profile_path()
    if not profile.exists():
        return False
    content = profile.read_text(encoding="utf-8", errors="ignore")
    return "# --- AGY PROXY HOOK START ---" in content

def _atomic_write_profile(profile: Path, content: str) -> None:
    import os, time
    temp_path = profile.with_suffix(f".tmp.{time.time()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, profile)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise

def install_hook(proxy_address: str) -> Tuple[bool, str]:
    profile = get_profile_path()
    profile.parent.mkdir(parents=True, exist_ok=True)
    
    content = ""
    if profile.exists():
        backup_file(profile)
        content = profile.read_text(encoding="utf-8", errors="ignore")
        
    if "# --- AGY PROXY HOOK START ---" in content:
        content = re.sub(r'(?s)\n?# --- AGY PROXY HOOK START ---.*?# --- AGY PROXY HOOK END ---\n?', '\n', content)
        
    content = content.rstrip() + "\n" + get_hook_script(proxy_address)
    _atomic_write_profile(profile, content)
    return True, str(profile)

def uninstall_hook() -> bool:
    profile = get_profile_path()
    if not profile.exists():
        return False
    
    content = profile.read_text(encoding="utf-8", errors="ignore")
    if "# --- AGY PROXY HOOK START ---" not in content:
        return False
        
    backup_file(profile)
    content = re.sub(r'(?s)\n?# --- AGY PROXY HOOK START ---.*?# --- AGY PROXY HOOK END ---\n?', '\n', content)
    _atomic_write_profile(profile, content)
    return True
