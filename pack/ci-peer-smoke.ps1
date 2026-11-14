# Two-instance cross-peer download through the built Hearth.exe.
# Starts two headless copies with separate --home profiles, shares a file on
# A, and downloads it from B both via GET /api/download (the old Get link)
# and POST /api/transfers/download (the UI path, which must never navigate
# the window). Single-instance locking is per data dir, so these two peers
# are real processes; there is no production bypass.
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
Remove-Item Env:HEARTH_ALLOW_MULTI -ErrorAction SilentlyContinue
$root = Join-Path $env:RUNNER_TEMP "hearth-peer-smoke-$(Get-Random)"
$homeA = Join-Path $root "a"
$homeB = Join-Path $root "b"
$share = Join-Path $root "share"
$downloads = Join-Path $root "downloads"
New-Item -ItemType Directory -Force -Path $homeA, $homeB, $share, $downloads | Out-Null
Set-Content -Path (Join-Path $share "hello.txt") -Value "from alpha" -Encoding utf8

function Get-PortPids([int]$Port) {
  $ids = @()
  try {
    $ids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {}
  return @($ids | Where-Object { $_ -and $_ -ne 0 })
}

function Stop-PortListeners([int]$Port) {
  foreach ($procId in Get-PortPids $Port) {
    Write-Host "Clearing leftover PID $procId on port $Port"
    try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
  }
  Start-Sleep -Milliseconds 200
}

function Write-HearthLog([string]$Label, [string]$HomeDir, $Proc) {
  Write-Host "----- $Label -----"
  if ($Proc) {
    try {
      Write-Host "pid=$($Proc.Id) hasExited=$($Proc.HasExited) exit=$(if ($Proc.HasExited) { $Proc.ExitCode } else { 'running' })"
    } catch {
      Write-Host "pid=unknown ($($_.Exception.Message))"
    }
  }
  foreach ($name in @("hearth.log", "stdout.log", "stderr.log")) {
    $path = Join-Path $HomeDir $name
    if (Test-Path $path) {
      Write-Host "----- $name -----"
      Get-Content -Path $path -ErrorAction SilentlyContinue
    } else {
      Write-Host "(no $name)"
    }
  }
}

function Write-DefenderHint([string[]]$Paths) {
  try {
    $since = (Get-Date).ToUniversalTime().AddMinutes(-15)
    $hits = @(Get-MpThreatDetection -ErrorAction SilentlyContinue | Where-Object {
      $_.InitialDetectionTime -and ([datetime]$_.InitialDetectionTime).ToUniversalTime() -ge $since
    })
    if ($hits.Count -eq 0) {
      Write-Host "No new Defender detections in the last 15 minutes."
      return
    }
    Write-Host "Recent Defender detections (informational):"
    foreach ($hit in $hits) {
      Write-Host ("  {0} {1} {2}" -f $hit.InitialDetectionTime, $hit.ThreatName, ($hit.Resources | Out-String).Trim())
    }
  } catch {
    Write-Host "Defender history unavailable: $($_.Exception.Message)"
  }
}

function Start-Hearth([int]$Port, [string]$HomeDir, [string]$Label) {
  Stop-PortListeners $Port
  $outLog = Join-Path $HomeDir "stdout.log"
  $errLog = Join-Path $HomeDir "stderr.log"
  $startParams = @{
    FilePath = $Exe
    ArgumentList = @("--no-desktop", "--no-browser", "--host", "127.0.0.1", "--port", "$Port", "--home", $HomeDir)
    WorkingDirectory = (Split-Path $Exe)
    PassThru = $true
    WindowStyle = "Hidden"
  }
  try {
    $startParams.RedirectStandardOutput = $outLog
    $startParams.RedirectStandardError = $errLog
    $proc = Start-Process @startParams
  } catch {
    $startParams.Remove("RedirectStandardOutput")
    $startParams.Remove("RedirectStandardError")
    $proc = Start-Process @startParams
  }
  $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
  $url = "http://127.0.0.1:$Port/api/state"
  $lastError = "not ready"
  while ((Get-Date) -lt $deadline) {
    if ($proc.HasExited) {
      Write-HearthLog $Label $HomeDir $proc
      throw "Hearth.exe on port $Port ($Label) exited early with code $($proc.ExitCode)"
    }
    try {
      $res = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -Headers @{ Connection = "close" }
      if ($res.StatusCode -eq 200) {
        $owners = Get-PortPids $Port
        if ($owners.Count -gt 0 -and -not ($owners -contains $proc.Id)) {
          Write-HearthLog $Label $HomeDir $proc
          throw "Hearth.exe pid $($proc.Id) started for port $Port but listener is owned by PID $($owners -join ',')"
        }
        return $proc
      }
    } catch {
      if ($_.Exception.Message -like "*exited early*" -or $_.Exception.Message -like "*listener is owned*") {
        throw
      }
      $lastError = $_.Exception.Message
      Start-Sleep -Milliseconds 200
    }
  }
  try { $proc.Kill() } catch {}
  Write-HearthLog $Label $HomeDir $proc
  throw "Hearth.exe on port $Port ($Label) did not become ready: $lastError"
}

function Invoke-Json([string]$Method, [string]$Url, $Body = $null, $Alive = $null) {
  $params = @{
    Method = $Method
    Uri = $Url
    UseBasicParsing = $true
    TimeoutSec = 15
    Headers = @{ Connection = "close" }
  }
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = ($Body | ConvertTo-Json -Compress -Depth 6)
  }
  $tries = 0
  $max = 4
  while ($true) {
    $tries += 1
    try {
      $res = Invoke-WebRequest @params
      if ($res.StatusCode -ge 400) {
        throw "$Method $Url failed: $($res.StatusCode) $($res.Content)"
      }
      if ($res.Content) {
        return $res.Content | ConvertFrom-Json
      }
      return $null
    } catch {
      $msg = $_.Exception.Message
      $transient = $msg -match "forcibly closed|existing connection was|connection reset|unexpectedly closed"
      if ($Alive) {
        foreach ($proc in @($Alive)) {
          if ($proc -and $proc.HasExited) {
            throw "$Method $Url failed because Hearth.exe pid $($proc.Id) exited with code $($proc.ExitCode): $msg"
          }
        }
      }
      if (-not $transient -or $tries -ge $max) {
        throw
      }
      Write-Host "Retrying $Method $Url after transient error ($tries/$max): $msg"
      Start-Sleep -Milliseconds 400
    }
  }
}

$procA = $null
$procB = $null
try {
  Write-Host "Starting two Hearth.exe instances on $PortA and $PortB (homes $homeA / $homeB)"
  $procA = Start-Hearth -Port $PortA -HomeDir $homeA -Label "peer-A"
  $procB = Start-Hearth -Port $PortB -HomeDir $homeB -Label "peer-B"
  Write-Host "Ready: A pid=$($procA.Id) B pid=$($procB.Id)"

  $created = Invoke-Json POST "http://127.0.0.1:$PortA/api/group/create" @{ name = "Home"; password = "ci-peer"; device_name = "Alpha" } @($procA, $procB)
  $invite = [string]$created.group.invite
  if ($invite -notlike "*127.0.0.1:$PortA*") {
    if ($invite.Split("|").Count -ge 6) { $invite = "$invite,127.0.0.1:$PortA" }
    else { $invite = "$invite|127.0.0.1:$PortA" }
  }
  $null = Invoke-Json POST "http://127.0.0.1:$PortB/api/group/invite" @{ invite = $invite; device_name = "Bravo" } @($procA, $procB)
  $null = Invoke-Json POST "http://127.0.0.1:$PortB/api/hosts" @{ host = "127.0.0.1:$PortA" } @($procA, $procB)

  $deadline = (Get-Date).AddSeconds(12)
  $ok = $false
  $namesA = @()
  $namesB = @()
  while ((Get-Date) -lt $deadline) {
    $stateA = Invoke-Json GET "http://127.0.0.1:$PortA/api/state" $null @($procA, $procB)
    $stateB = Invoke-Json GET "http://127.0.0.1:$PortB/api/state" $null @($procA, $procB)
    $namesA = @($stateA.members | Where-Object { $_.online } | ForEach-Object { $_.name })
    $namesB = @($stateB.members | Where-Object { $_.online } | ForEach-Object { $_.name })
    if ($namesA -contains "Bravo" -and $namesB -contains "Alpha") { $ok = $true; break }
    Start-Sleep -Milliseconds 250
  }
  if (-not $ok) { throw "Peers did not discover each other (A=$namesA B=$namesB)" }

  $shared = Invoke-Json POST "http://127.0.0.1:$PortA/api/shares" @{ path = $share; label = "Notes"; mode = "read" } @($procA, $procB)
  $alphaId = $shared.device.id
  $notes = @($shared.shares | Where-Object { $_.label -eq "Notes" })[0]

  $listing = Invoke-Json GET "http://127.0.0.1:$PortB/api/files/$alphaId/$($notes.id)" $null @($procA, $procB)
  $names = @($listing.items | ForEach-Object { $_.name })
  if ($names -notcontains "hello.txt") { throw "Remote listing missed hello.txt: $names" }

  $download = Invoke-WebRequest -Uri "http://127.0.0.1:$PortB/api/download/$alphaId/$($notes.id)?path=hello.txt" -UseBasicParsing -TimeoutSec 20 -Headers @{ Connection = "close" }
  if ($download.StatusCode -ne 200) { throw "GET /api/download returned $($download.StatusCode)" }
  if ($download.Content -notlike "*from alpha*") { throw "Downloaded body did not match" }
  if ($download.Content -like "*Internal Server Error*") { throw "Download returned Internal Server Error" }

  $null = Invoke-Json POST "http://127.0.0.1:$PortB/api/settings" @{ download_folder = $downloads } @($procA, $procB)
  $job = Invoke-Json POST "http://127.0.0.1:$PortB/api/transfers/download" @{
    device_id = $alphaId
    share_id = $notes.id
    path = "hello.txt"
  } @($procA, $procB)
  $tid = $job.started[0].id
  $deadline = (Get-Date).AddSeconds(10)
  $status = $null
  while ((Get-Date) -lt $deadline) {
    $listing = Invoke-Json GET "http://127.0.0.1:$PortB/api/transfers" $null @($procA, $procB)
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
catch {
  Write-Host "Cross-peer smoke failed: $($_.Exception.Message)"
  Write-HearthLog "peer-A" $homeA $procA
  Write-HearthLog "peer-B" $homeB $procB
  Write-DefenderHint @($Exe, $homeA, $homeB, $root)
  throw
}
finally {
  foreach ($proc in @($procA, $procB)) {
    if ($proc -and -not $proc.HasExited) {
      try { $proc.Kill() } catch {}
    }
  }
}
