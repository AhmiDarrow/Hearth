# Starts Hearth.exe hidden on its own profile and port, checks it serves, and asks it to quit.
# Fails the workflow if it does not start, does not serve, or does not quit when asked.
param(
  [Parameter(Mandatory = $true)][string]$Exe,
  [Parameter(Mandatory = $true)][int]$Port,
  [Parameter(Mandatory = $true)][string]$HomeDir,
  [int]$TimeoutMs = 60000
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "ci-hidden.ps1")

if (-not (Test-Path $Exe)) { throw "Hearth.exe not found at $Exe" }
$exePath = (Resolve-Path $Exe).Path
if (Test-Path $HomeDir) { Remove-Item $HomeDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

$env:HEARTH_NO_UPDATE = "1"
$env:HEARTH_CI = "1"
$appArgs = @("--background", "--port", "$Port", "--home", $HomeDir)
Write-Host "Smoke testing $exePath $($appArgs -join ' ')"
$proc = Start-HiddenProcess -FilePath $exePath -ArgumentList $appArgs -WorkingDirectory (Split-Path $exePath)

# Hearth serves the other PCs only, and refuses a caller that has no home group's key: any
# answer from /peer/ping is Hearth serving.
$served = $false
$deadline = (Get-Date).AddMilliseconds($TimeoutMs)
while (-not $served -and (Get-Date) -lt $deadline) {
  if ($proc.HasExited) { break }
  try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$Port/peer/ping" -UseBasicParsing -TimeoutSec 3 | Out-Null
    $served = $true
  } catch {
    if ($_.Exception.Response) { $served = $true } else { Start-Sleep -Milliseconds 400 }
  }
}
$logPath = Join-Path $HomeDir "hearth.log"
if (-not $served) {
  if (Test-Path $logPath) { Get-Content $logPath }
  try { $proc.Kill() } catch {}
  throw "Hearth.exe did not serve on port $Port within ${TimeoutMs}ms (exited: $($proc.HasExited))"
}
Write-Host "Hearth is serving on port $Port"

# A second launch with --quit hands the request to the one running on this profile.
$quit = Start-HiddenProcess -FilePath $exePath -ArgumentList @("--quit", "--port", "$Port", "--home", $HomeDir) -WorkingDirectory (Split-Path $exePath) -Wait -TimeoutSec 20
if (-not $proc.WaitForExit(20000)) {
  try { $proc.Kill() } catch {}
  throw "Hearth.exe did not quit within 20s of --quit (the second launch exited $($quit.ExitCode))"
}
if (Test-Path $logPath) {
  Write-Host "----- hearth.log -----"
  Get-Content $logPath
  Write-Host "----- end hearth.log -----"
} else {
  throw "Hearth.exe wrote no hearth.log in $HomeDir"
}
Write-Host "Hearth.exe started, served and quit when asked"
