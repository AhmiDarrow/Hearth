# Two-instance cross-peer download through the built Hearth.exe.
# Starts two headless copies, shares a file on A, and downloads it from B
# both via GET /api/download (the old Get link) and POST /api/transfers/download
# (the UI path, which must never navigate the window).
param(
  [Parameter(Mandatory = $true)]
  [string]$Exe,
  [int]$PortA = 18474,
  [int]$PortB = 18475,
  [int]$TimeoutMs = 60000
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Exe)) {
  throw "Hearth.exe not found: $Exe"
}

$env:HEARTH_NO_UPDATE = "1"
$env:HEARTH_ALLOW_MULTI = "1"
$root = Join-Path $env:RUNNER_TEMP "hearth-peer-smoke-$(Get-Random)"
$homeA = Join-Path $root "a"
$homeB = Join-Path $root "b"
$share = Join-Path $root "share"
$downloads = Join-Path $root "downloads"
New-Item -ItemType Directory -Force -Path $homeA, $homeB, $share, $downloads | Out-Null
Set-Content -Path (Join-Path $share "hello.txt") -Value "from alpha" -Encoding utf8

function Start-Hearth([int]$Port, [string]$HomeDir) {
  $proc = Start-Process -FilePath $Exe `
    -ArgumentList @("--no-desktop", "--no-browser", "--host", "127.0.0.1", "--port", "$Port", "--home", $HomeDir) `
    -WorkingDirectory (Split-Path $Exe) `
    -PassThru `
    -WindowStyle Hidden
  $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
  $url = "http://127.0.0.1:$Port/api/state"
  while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
      throw "Hearth.exe on port $Port exited early with code $($proc.ExitCode)"
    }
    try {
      $res = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
      if ($res.StatusCode -eq 200) { return $proc }
    } catch {
      Start-Sleep -Milliseconds 200
    }
  }
  try { $proc.Kill() } catch {}
  throw "Hearth.exe on port $Port did not become ready"
}

function Invoke-Json([string]$Method, [string]$Url, $Body = $null) {
  $params = @{
    Method = $Method
    Uri = $Url
    UseBasicParsing = $true
    TimeoutSec = 15
  }
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = ($Body | ConvertTo-Json -Compress -Depth 6)
  }
  $res = Invoke-WebRequest @params
  if ($res.StatusCode -ge 400) {
    throw "$Method $Url failed: $($res.StatusCode) $($res.Content)"
  }
  if ($res.Content) {
    return $res.Content | ConvertFrom-Json
  }
  return $null
}

$procA = $null
$procB = $null
try {
  Write-Host "Starting two Hearth.exe instances on $PortA and $PortB"
  $procA = Start-Hearth -Port $PortA -HomeDir $homeA
  $procB = Start-Hearth -Port $PortB -HomeDir $homeB

  $created = Invoke-Json POST "http://127.0.0.1:$PortA/api/group/create" @{ name = "Home"; password = "ci-peer"; device_name = "Alpha" }
  $invite = [string]$created.group.invite
  if ($invite -notlike "*127.0.0.1:$PortA*") {
    if ($invite.Split("|").Count -ge 6) { $invite = "$invite,127.0.0.1:$PortA" }
    else { $invite = "$invite|127.0.0.1:$PortA" }
  }
  $null = Invoke-Json POST "http://127.0.0.1:$PortB/api/group/invite" @{ invite = $invite; device_name = "Bravo" }
  $null = Invoke-Json POST "http://127.0.0.1:$PortB/api/hosts" @{ host = "127.0.0.1:$PortA" }

  $deadline = (Get-Date).AddSeconds(12)
  $ok = $false
  while ((Get-Date) -lt $deadline) {
    $stateA = Invoke-Json GET "http://127.0.0.1:$PortA/api/state"
    $stateB = Invoke-Json GET "http://127.0.0.1:$PortB/api/state"
    $namesA = @($stateA.members | Where-Object { $_.online } | ForEach-Object { $_.name })
    $namesB = @($stateB.members | Where-Object { $_.online } | ForEach-Object { $_.name })
    if ($namesA -contains "Bravo" -and $namesB -contains "Alpha") { $ok = $true; break }
    Start-Sleep -Milliseconds 250
  }
  if (-not $ok) { throw "Peers did not discover each other (A=$namesA B=$namesB)" }

  $shared = Invoke-Json POST "http://127.0.0.1:$PortA/api/shares" @{ path = $share; label = "Notes"; mode = "read" }
  $alphaId = $shared.device.id
  $notes = @($shared.shares | Where-Object { $_.label -eq "Notes" })[0]

  $listing = Invoke-Json GET "http://127.0.0.1:$PortB/api/files/$alphaId/$($notes.id)"
  $names = @($listing.items | ForEach-Object { $_.name })
  if ($names -notcontains "hello.txt") { throw "Remote listing missed hello.txt: $names" }

  $download = Invoke-WebRequest -Uri "http://127.0.0.1:$PortB/api/download/$alphaId/$($notes.id)?path=hello.txt" -UseBasicParsing -TimeoutSec 20
  if ($download.StatusCode -ne 200) { throw "GET /api/download returned $($download.StatusCode)" }
  if ($download.Content -notlike "*from alpha*") { throw "Downloaded body did not match" }
  if ($download.Content -like "*Internal Server Error*") { throw "Download returned Internal Server Error" }

  $null = Invoke-Json POST "http://127.0.0.1:$PortB/api/settings" @{ download_folder = $downloads }
  $job = Invoke-Json POST "http://127.0.0.1:$PortB/api/transfers/download" @{
    device_id = $alphaId
    share_id = $notes.id
    path = "hello.txt"
  }
  $tid = $job.started[0].id
  $deadline = (Get-Date).AddSeconds(10)
  $status = $null
  while ((Get-Date) -lt $deadline) {
    $listing = Invoke-Json GET "http://127.0.0.1:$PortB/api/transfers"
    $found = @($listing.transfers | Where-Object { $_.id -eq $tid })[0]
    if ($found -and ($found.status -eq "done" -or $found.status -eq "error")) { $status = $found; break }
    Start-Sleep -Milliseconds 100
  }
  if (-not $status -or $status.status -ne "done") { throw "Background transfer failed: $($status | ConvertTo-Json -Compress)" }
  $saved = Join-Path $downloads "hello.txt"
  if (-not (Test-Path $saved)) { throw "Transfer finished but $saved is missing" }
  $text = Get-Content -Raw $saved
  if ($text -notlike "*from alpha*") { throw "Saved file did not match: $text" }

  Write-Host "Cross-peer download smoke passed."
}
finally {
  foreach ($proc in @($procA, $procB)) {
    if ($proc -and -not $proc.HasExited) {
      try { $proc.Kill() } catch {}
    }
  }
}
