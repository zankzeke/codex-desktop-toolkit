"""Launch Antigravity Desktop with a process-scoped HTTP proxy on Windows."""

import os
import subprocess
from pathlib import Path

from powershell_hook import validate_proxy_address


SHORTCUT_NAME = "Antigravity (Toolkit Proxy).lnk"

_SAVE_SHORTCUT = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw 'Windows desktop folder is unavailable.'
}
$path = Join-Path $desktop $env:TOOLKIT_AGY_LINK_NAME
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = $env:TOOLKIT_AGY_LINK_TARGET
$link.Arguments = $env:TOOLKIT_AGY_LINK_ARGS
$link.WorkingDirectory = [IO.Path]::GetDirectoryName($env:TOOLKIT_AGY_LINK_ICON)
$link.IconLocation = "$env:TOOLKIT_AGY_LINK_ICON,0"
$link.Description = 'Launch Antigravity with the proxy configured in Codex Bridge Toolkit'
$link.WindowStyle = 7
$link.Save()
Write-Output $path
"""


def find_antigravity_exe() -> Path | None:
    for variable, location in (
        ("LOCALAPPDATA", "Programs/antigravity"),
        ("ProgramFiles", "Antigravity"),
        ("ProgramFiles(x86)", "Antigravity"),
    ):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / location / "Antigravity.exe"
            if candidate.is_file():
                return candidate
    return None


def _powershell() -> Path:
    return Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def _launch_arguments(executable: Path, proxy_address: str) -> list[str]:
    validate_proxy_address(proxy_address)
    if not executable.is_absolute() or executable.name.lower() != "antigravity.exe" or not executable.is_file():
        raise ValueError("请选择已安装的 Antigravity.exe。")

    # PowerShell single-quoted strings treat a doubled apostrophe as literal.
    exe_literal = "'" + str(executable).replace("'", "''") + "'"
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
    $session = (Get-Process -Id $PID).SessionId
    $running = Get-Process -Name Antigravity -ErrorAction SilentlyContinue |
        Where-Object SessionId -eq $session
    if ($running) {{
        throw 'Antigravity 已经在运行。请保存工作，完全退出 Antigravity（包括托盘），再使用代理入口启动。'
    }}
    $env:HTTP_PROXY = $env:HTTPS_PROXY = 'http://{proxy_address}'
    $env:NO_PROXY = 'localhost,127.0.0.1,::1'
    Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue
    $exe = {exe_literal}
    Start-Process -FilePath $exe -WorkingDirectory ([IO.Path]::GetDirectoryName($exe))
}} catch {{
    Add-Type -AssemblyName System.Windows.Forms
    [void][Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Antigravity 代理启动')
    exit 1
}}
"""
    return ["-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script.strip()]


def launch_antigravity(executable: Path, proxy_address: str) -> None:
    subprocess.Popen(
        [str(_powershell()), *_launch_arguments(executable, proxy_address)],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def create_antigravity_shortcut(executable: Path, proxy_address: str) -> Path:
    arguments = subprocess.list2cmdline(_launch_arguments(executable, proxy_address))
    # WScript.Shell silently truncates arguments that exceed its 1023 UTF-16 code unit limit.
    if len(arguments.encode("utf-16-le")) > 2 * 1023:
        raise ValueError("程序路径或代理地址过长，无法写入 Windows 快捷方式。仍可从 Toolkit 启动。")
    env = os.environ.copy()
    env.update(
        TOOLKIT_AGY_LINK_NAME=SHORTCUT_NAME,
        TOOLKIT_AGY_LINK_TARGET=str(_powershell()),
        TOOLKIT_AGY_LINK_ARGS=arguments,
        TOOLKIT_AGY_LINK_ICON=str(executable),
    )
    result = subprocess.run(
        [str(_powershell()), "-NoProfile", "-NonInteractive", "-Command", _SAVE_SHORTCUT],
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=12,
    )
    if result.returncode or not result.stdout.strip():
        raise OSError("无法在当前用户的 Windows 桌面创建快捷方式。")
    shortcut = Path(result.stdout.strip())
    if not shortcut.is_file():
        raise OSError("Windows 未生成快捷方式文件。")
    return shortcut
