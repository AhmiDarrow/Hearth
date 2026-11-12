#Requires -Version 5.1
param(
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$candidates = @(
    (Join-Path $env:LOCALAPPDATA "Hearth\venv\Scripts\pythonw.exe"),
    (Join-Path $PSScriptRoot "venv\Scripts\pythonw.exe"),
    (Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe")
)
$pythonw = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
$workdir = $PSScriptRoot
if ($pythonw -and ($pythonw -like "*\Hearth\venv\*")) {
    $workdir = Join-Path $env:LOCALAPPDATA "Hearth"
}
if (-not $pythonw) {
    Write-Host "Hearth is not installed yet. Double-click install.cmd first."
    exit 1
}
$hearthArgs = @("-m", "hearth")
if ($NoBrowser) { $hearthArgs += "--no-browser" }
Start-Process -FilePath $pythonw -ArgumentList $hearthArgs -WorkingDirectory $workdir
