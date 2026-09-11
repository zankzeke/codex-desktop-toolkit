# start.ps1 — Launch the Codex ID compatibility proxy
# Usage: .\start.ps1 [-Port 8787] [-ReasoningMode safe]

param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8787,
    [ValidateSet("safe", "drop_invalid")]
    [string]$ReasoningMode = "safe",
    [string]$Upstream = "https://chatgpt.com/backend-api/codex",
    [string]$UpstreamProxy = "",
    [string]$LogLevel = "INFO"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Format-SafeUrl([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $Value }
    try {
        $uri = [Uri]$Value
        $hostPart = $uri.Host
        if ($hostPart.Contains(":")) { $hostPart = "[$hostPart]" }
        if (-not $uri.IsDefaultPort) { $hostPart = "$hostPart`:$($uri.Port)" }
        if (-not [string]::IsNullOrEmpty($uri.UserInfo)) { $hostPart = "***:***@$hostPart" }
        return "$($uri.Scheme)://$hostPart$($uri.AbsolutePath)"
    } catch {
        return "<invalid-url>"
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Codex ID compatibility proxy" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

try {
    $pyVersion = python --version 2>&1
    Write-Host "  Python: $pyVersion" -ForegroundColor Green
} catch {
    Write-Error "Python not found. Please install Python 3.11+."
    exit 1
}

$VenvDir = Join-Path $ScriptDir ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "  Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $VenvDir
}

$PipExe = Join-Path $VenvDir "Scripts\pip.exe"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "  Installing/checking dependencies..." -ForegroundColor Yellow
& $PipExe install -q -r (Join-Path $ScriptDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed."
    exit 1
}

Write-Host ""
Write-Host "  Listening : http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "  Upstream  : $(Format-SafeUrl $Upstream)" -ForegroundColor Cyan
if ($UpstreamProxy) { Write-Host "  Proxy     : $(Format-SafeUrl $UpstreamProxy)" -ForegroundColor Cyan }
Write-Host "  Reasoning : $ReasoningMode" -ForegroundColor Cyan
Write-Host "  Health    : http://127.0.0.1:$Port/health" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

$ArgsList = @(
    (Join-Path $ScriptDir "proxy.py"),
    "--port", $Port,
    "--upstream", $Upstream,
    "--reasoning-mode", $ReasoningMode,
    "--log-level", $LogLevel
)
if ($UpstreamProxy) {
    $ArgsList += "--upstream-proxy"
    $ArgsList += $UpstreamProxy
}

& $PythonExe @ArgsList
