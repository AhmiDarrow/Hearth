# CI smoke test for the built (and optionally installed) Windows app.
# Fails the workflow if Hearth.exe cannot start or serve /api/state.
param(
  [Parameter(Mandatory = $true)]
  [string]$Exe,
  [string]$HomeDir = "",
  [int]$Port = 17474,
  [int]$TimeoutMs = 60000
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Exe)) {
  throw "Hearth.exe not found: $Exe"
}
if (-not $HomeDir) {
  $HomeDir = Join-Path $env:RUNNER_TEMP "hearth-smoke-$(Get-Random)"
}
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
$env:HEARTH_NO_UPDATE = "1"
Write-Host "Smoke testing $Exe --self-test --port $Port --home $HomeDir"
$proc = Start-Process -FilePath $Exe `
  -ArgumentList @("--self-test", "--port", "$Port", "--home", $HomeDir, "--host", "127.0.0.1") `
  -WorkingDirectory (Split-Path $Exe) `
  -PassThru `
  -WindowStyle Hidden
if (-not $proc.WaitForExit($TimeoutMs)) {
  try { $proc.Kill() } catch {}
  throw "Hearth.exe --self-test timed out after ${TimeoutMs}ms"
}
$logPath = Join-Path $HomeDir "self-test.log"
if (Test-Path $logPath) {
  Write-Host "----- self-test.log -----"
  Get-Content $logPath
  Write-Host "----- end self-test.log -----"
}
if ($proc.ExitCode -ne 0) {
  throw "Hearth.exe --self-test exited with code $($proc.ExitCode)"
}
Write-Host "Smoke test passed (exit 0)."
