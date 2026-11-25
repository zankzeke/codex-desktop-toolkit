import json
import os
import subprocess
import sys

import pytest

from antigravity_launcher import _launch_arguments, _powershell


@pytest.mark.skipif(sys.platform != "win32", reason="Uses Windows PowerShell")
def test_desktop_launcher_passes_proxy_and_literal_path_to_child(tmp_path):
    exe = tmp_path / "Google's Apps" / "Antigravity.exe"
    exe.parent.mkdir()
    exe.touch()
    launcher = _launch_arguments(exe, "127.0.0.1:10808")
    # Execute the launcher, substituting the OS process calls to avoid opening an app.
    harness = """
$ErrorActionPreference = 'Stop'
function Get-Process {
    [CmdletBinding()]
    param($Id, $Name)
    if ($Id) { [pscustomobject]@{SessionId = 1} }
}
function Start-Process($FilePath, $WorkingDirectory) {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    [pscustomobject]@{
        exe = $FilePath
        cwd = $WorkingDirectory
        http = $env:HTTP_PROXY
        https = $env:HTTPS_PROXY
        no = $env:NO_PROXY
        all = $env:ALL_PROXY
    } | ConvertTo-Json -Compress
}
function Add-Type { throw 'The launcher failed before Start-Process' }
"""
    result = subprocess.run(
        [str(_powershell()), *launcher[:-1], harness + launcher[-1]],
        env={**os.environ, "ALL_PROXY": "socks5://127.0.0.1:9000", "NO_PROXY": "*"},
        capture_output=True,
        encoding="utf-8-sig",
        check=True,
        timeout=12,
    )
    assert json.loads(result.stdout) == {
        "exe": str(exe),
        "cwd": str(exe.parent),
        "http": "http://127.0.0.1:10808",
        "https": "http://127.0.0.1:10808",
        "no": "localhost,127.0.0.1,::1",
        "all": None,
    }


def test_desktop_launcher_rejects_missing_executable(tmp_path):
    with pytest.raises(ValueError, match=r"Antigravity\.exe"):
        _launch_arguments(tmp_path / "Antigravity.exe", "127.0.0.1:10808")
