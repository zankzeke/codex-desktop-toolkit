# start-gui.ps1 — Bootstrap and launch the Codex Desktop Toolkit GUI
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -Path $ScriptDir

function Resolve-Python {
    foreach ($candidate in @("python", "py")) {
        try {
            if ($candidate -eq "py") {
                & py -3.11 --version *> $null
                if ($LASTEXITCODE -eq 0) { return @("py", "-3.11") }
            } else {
                & python --version *> $null
                if ($LASTEXITCODE -eq 0) { return @("python") }
            }
        } catch { }
    }
    throw "Python 3.11+ was not found. Install Python and retry."
}

$VenvDir = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    $launcher = Resolve-Python
    if ($launcher.Count -eq 1) {
        & $launcher[0] -m venv $VenvDir
    } else {
        & $launcher[0] $launcher[1] -m venv $VenvDir
    }
}

$PipExe = Join-Path $VenvDir "Scripts\pip.exe"
Write-Host "Installing/checking dependencies..." -ForegroundColor Yellow
& $PipExe install -q -r (Join-Path $ScriptDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

Write-Host "Starting Codex Toolkit GUI..." -ForegroundColor Cyan
Start-Process -FilePath $VenvPython -ArgumentList @((Join-Path $ScriptDir "codex_toolkit_gui.py"))
