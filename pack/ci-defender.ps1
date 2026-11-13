# Scan built Hearth binaries with Microsoft Defender, and optionally launch
# the app under real-time protection for a short runtime watch.
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Path,
    [string]$LaunchExe = "",
    [int]$RuntimeSeconds = 0,
    [string]$HomeDir = "",
    [int]$Port = 17475
)

$ErrorActionPreference = "Stop"

function Get-MpCmd {
    $candidates = @(
        "${env:ProgramFiles}\Windows Defender\MpCmdRun.exe",
        "${env:ProgramFiles}\Microsoft\Windows Defender\MpCmdRun.exe",
        "${env:ProgramFiles(x86)}\Windows Defender\MpCmdRun.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    throw "MpCmdRun.exe was not found; cannot run the Defender scan."
}

function Get-ThreatSnapshot {
    $detections = @()
    try { $detections = @(Get-MpThreatDetection -ErrorAction SilentlyContinue) } catch { $detections = @() }
    $threats = @()
    try { $threats = @(Get-MpThreat -ErrorAction SilentlyContinue) } catch { $threats = @() }
    return [pscustomobject]@{ Detections = $detections; Threats = $threats }
}

function Format-Threats($snapshot) {
    $lines = @()
    foreach ($t in $snapshot.Threats) {
        $lines += ("threat Id={0} Name={1} Severity={2} Status={3}" -f $t.ThreatID, $t.ThreatName, $t.SeverityID, $t.ThreatStatusID)
    }
    foreach ($d in $snapshot.Detections) {
        $res = @($d.Resources) -join ", "
        $lines += ("detection Id={0} Threat={1} Time={2} Resources={3}" -f $d.ThreatID, $d.ThreatName, $d.InitialDetectionTime, $res)
    }
    if (-not $lines) { return "(none)" }
    return ($lines -join "`n")
}

function Test-IsHearthRelated($snapshot, [string[]]$needles) {
    $blob = (Format-Threats $snapshot).ToLowerInvariant()
    foreach ($n in $needles) {
        if ($n -and $blob.Contains($n.ToLowerInvariant())) { return $true }
    }
    return $false
}

$mp = Get-MpCmd
Write-Host "Using $mp"

try {
    Write-Host "Updating Defender signatures..."
    & $mp -SignatureUpdate | Out-Host
} catch {
    Write-Warning "Signature update failed: $_"
}
try {
    Update-MpSignature -ErrorAction SilentlyContinue
} catch {}

# Make sure real-time protection is on for the runtime watch.
try {
    Set-MpPreference -DisableRealtimeMonitoring $false -ErrorAction SilentlyContinue
} catch {}

$before = Get-ThreatSnapshot
Write-Host "Threats before scan:`n$(Format-Threats $before)"

foreach ($item in $Path) {
    if (-not (Test-Path $item)) { throw "Defender scan path missing: $item" }
    $full = (Resolve-Path $item).Path
    Write-Host "MpCmdRun -Scan -ScanType 3 -File $full"
    & $mp -Scan -ScanType 3 -File $full
    $code = $LASTEXITCODE
    Write-Host "Defender scan exit code $code for $full"
    # 0 = no threats, 2 = threats found. Other codes are tool errors.
    if ($code -eq 2) {
        throw "Microsoft Defender reported a threat in $full"
    }
    if ($code -ne 0) {
        throw "Microsoft Defender scan failed for $full with exit code $code"
    }
}

$afterScan = Get-ThreatSnapshot
Write-Host "Threats after static scan:`n$(Format-Threats $afterScan)"
$needles = @("Hearth", "Hearth.exe", "HearthSetup.exe")
$needles += @($Path | ForEach-Object { try { (Resolve-Path $_).Path } catch { $_ } })
if (Test-IsHearthRelated $afterScan $needles) {
    throw "Microsoft Defender listed a Hearth file after the static scan:`n$(Format-Threats $afterScan)"
}

if ($LaunchExe) {
    if (-not (Test-Path $LaunchExe)) { throw "Launch exe missing: $LaunchExe" }
    if ($RuntimeSeconds -le 0) { $RuntimeSeconds = 60 }
    $exe = (Resolve-Path $LaunchExe).Path
    Write-Host "Runtime watch: launching $exe for $RuntimeSeconds seconds under real-time protection"
    $env:HEARTH_NO_UPDATE = "1"
    $argList = @("--background", "--port", "$Port")
    if ($HomeDir) {
        New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
        $argList += @("--home", $HomeDir)
    }
    $proc = Start-Process -FilePath $exe -ArgumentList $argList -PassThru
    try {
        $deadline = (Get-Date).AddSeconds($RuntimeSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            $live = Get-ThreatSnapshot
            if (Test-IsHearthRelated $live $needles) {
                throw "Microsoft Defender flagged Hearth during the runtime watch:`n$(Format-Threats $live)"
            }
            if ($proc.HasExited -and $proc.ExitCode -ne 0) {
                Write-Warning "Hearth exited early with code $($proc.ExitCode)"
                break
            }
        }
    } finally {
        Get-Process -Name "Hearth" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 8
    }
    $afterRun = Get-ThreatSnapshot
    Write-Host "Threats after runtime watch:`n$(Format-Threats $afterRun)"
    if (Test-IsHearthRelated $afterRun $needles) {
        throw "Microsoft Defender flagged Hearth after the runtime watch:`n$(Format-Threats $afterRun)"
    }
}

Write-Host "Defender checks passed."
