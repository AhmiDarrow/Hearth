# Scan built Hearth binaries with Microsoft Defender, and optionally launch
# the app under real-time protection for a short runtime watch.
#
# Fail only on detections whose timestamp is after this job's start AND whose
# path is in this run's build, install, or E2E directories. Older or unrelated
# Defender history is logged as informational and never fails the job.
# This script does not clear history, add exclusions, or change Defender settings.
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Path,
    [string]$LaunchExe = "",
    [int]$RuntimeSeconds = 0,
    [string]$HomeDir = "",
    [int]$Port = 17475
)

$ErrorActionPreference = "Stop"
$JobStart = Get-Date
if ($env:HEARTH_JOB_START) {
    try { $JobStart = [datetime]::Parse($env:HEARTH_JOB_START, $null, [Globalization.DateTimeStyles]::RoundtripKind) } catch {
        Write-Warning "Could not parse HEARTH_JOB_START='$($env:HEARTH_JOB_START)'; using script start $JobStart"
    }
}
# If the env stamp was recorded without a kind, treat unspecified as local time.
if ($JobStart.Kind -eq [DateTimeKind]::Unspecified) {
    $JobStart = [DateTime]::SpecifyKind($JobStart, [DateTimeKind]::Local)
}

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

function Add-ScopeRoot([System.Collections.Generic.List[string]]$roots, [string]$path) {
    if (-not $path) { return }
    $candidate = $path
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path
        if ($item.PSIsContainer) { $candidate = $item.FullName }
        else { $candidate = $item.DirectoryName }
    }
    try { $candidate = [IO.Path]::GetFullPath($candidate) } catch { return }
    $norm = $candidate.TrimEnd('\', '/').ToLowerInvariant()
    if (-not $norm) { return }
    if ($roots | Where-Object { $_ -eq $norm }) { return }
    [void]$roots.Add($norm)
}

function Get-ScopeRoots {
    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($item in $Path) { Add-ScopeRoot $roots $item }
    Add-ScopeRoot $roots $LaunchExe
    Add-ScopeRoot $roots $HomeDir
    if ($env:GITHUB_WORKSPACE) {
        Add-ScopeRoot $roots $env:GITHUB_WORKSPACE
        Add-ScopeRoot $roots (Join-Path $env:GITHUB_WORKSPACE "dist")
    }
    if (Test-Path -LiteralPath "dist") { Add-ScopeRoot $roots (Resolve-Path "dist").Path }
    Add-ScopeRoot $roots (Join-Path ${env:ProgramFiles} "Hearth")
    Add-ScopeRoot $roots (Join-Path ${env:ProgramFiles(x86)} "Hearth")
    if ($env:LOCALAPPDATA) { Add-ScopeRoot $roots (Join-Path $env:LOCALAPPDATA "Programs\Hearth") }
    if ($env:RUNNER_TEMP -and (Test-Path -LiteralPath $env:RUNNER_TEMP)) {
        Get-ChildItem -LiteralPath $env:RUNNER_TEMP -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "hearth-*" } |
            ForEach-Object { Add-ScopeRoot $roots $_.FullName }
    }
    if ($env:HEARTH_E2E_EVIDENCE) { Add-ScopeRoot $roots $env:HEARTH_E2E_EVIDENCE }
    return @($roots)
}

function Get-ResourcePaths($detection) {
    $out = @()
    foreach ($raw in @($detection.Resources)) {
        $s = [string]$raw
        if ($s -match '^(file|container|directory|sharedfolder):_(.+)$') {
            $out += $Matches[2]
        } elseif ($s -match '^[A-Za-z]:\\' -or $s.StartsWith('\\')) {
            $out += $s
        }
    }
    return $out
}

function Test-UnderScope([string]$path, [string[]]$roots) {
    if (-not $path) { return $false }
    try { $full = [IO.Path]::GetFullPath($path) } catch { $full = $path }
    $norm = $full.TrimEnd('\', '/').ToLowerInvariant()
    foreach ($root in $roots) {
        if (-not $root) { continue }
        if ($norm -eq $root -or $norm.StartsWith($root + '\') -or $norm.StartsWith($root + '/')) { return $true }
    }
    return $false
}

function Get-DetectionTime($detection) {
    $value = $detection.InitialDetectionTime
    if (-not $value) { $value = $detection.LastThreatStatusChangeTime }
    if (-not $value) { return $null }
    $time = [datetime]$value
    if ($time.Kind -eq [DateTimeKind]::Unspecified) {
        $time = [DateTime]::SpecifyKind($time, [DateTimeKind]::Local)
    }
    return $time
}

function Split-Detections($snapshot, [string[]]$roots, [datetime]$notBefore) {
    $inScope = @()
    $ignored = @()
    foreach ($d in @($snapshot.Detections)) {
        $paths = @(Get-ResourcePaths $d)
        $scoped = @($paths | Where-Object { Test-UnderScope $_ $roots })
        $time = Get-DetectionTime $d
        $recent = $false
        if ($time) {
            $recent = $time.ToUniversalTime() -ge $notBefore.ToUniversalTime()
        }
        if ($scoped.Count -gt 0 -and $recent) {
            $inScope += $d
        } else {
            $ignored += $d
        }
    }
    return [pscustomobject]@{ InScope = $inScope; Ignored = $ignored }
}

function Format-DetectionList($detections) {
    $lines = @()
    foreach ($d in @($detections)) {
        $res = @($d.Resources) -join ", "
        $lines += ("detection Id={0} Threat={1} Time={2} Resources={3}" -f $d.ThreatID, $d.ThreatName, $d.InitialDetectionTime, $res)
    }
    if (-not $lines) { return "(none)" }
    return ($lines -join "`n")
}

function Assert-NoNewInScope($snapshot, [string[]]$roots, [datetime]$notBefore, [string]$phase) {
    $split = Split-Detections $snapshot $roots $notBefore
    Write-Host "Historical or out-of-scope detections (informational, ignored) after ${phase}:`n$(Format-DetectionList $split.Ignored)"
    Write-Host "In-scope detections after job start (${phase}):`n$(Format-DetectionList $split.InScope)"
    if ($split.InScope.Count -gt 0) {
        throw "Microsoft Defender listed a new in-scope Hearth file after the job start (${phase}):`n$(Format-DetectionList $split.InScope)"
    }
}

$mp = Get-MpCmd
Write-Host "Using $mp"
Write-Host "Defender job start used for gating: $($JobStart.ToUniversalTime().ToString('o')) (local $JobStart)"
$scopeRoots = Get-ScopeRoots
Write-Host "Defender scope roots:`n$($scopeRoots -join "`n")"

try {
    Write-Host "Updating Defender signatures..."
    & $mp -SignatureUpdate | Out-Host
} catch {
    Write-Warning "Signature update failed: $_"
}
try {
    Update-MpSignature -ErrorAction SilentlyContinue
} catch {}

$before = Get-ThreatSnapshot
Write-Host "All Defender threats/detections at step start (informational):`n$(Format-Threats $before)"
Assert-NoNewInScope $before $scopeRoots $JobStart "step start"

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
Write-Host "All Defender threats/detections after static scan (informational):`n$(Format-Threats $afterScan)"
Assert-NoNewInScope $afterScan $scopeRoots $JobStart "static scan"

if ($LaunchExe) {
    if (-not (Test-Path $LaunchExe)) { throw "Launch exe missing: $LaunchExe" }
    if ($RuntimeSeconds -le 0) { $RuntimeSeconds = 60 }
    $exe = (Resolve-Path $LaunchExe).Path
    Write-Host "Runtime watch: launching $exe for $RuntimeSeconds seconds under real-time protection"
    $env:HEARTH_NO_UPDATE = "1"
    $argList = @("--no-desktop", "--no-browser", "--host", "127.0.0.1", "--port", "$Port")
    if ($HomeDir) {
        New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
        $argList += @("--home", $HomeDir)
    }
    $proc = Start-Process -FilePath $exe -ArgumentList $argList -PassThru -WindowStyle Hidden
    try {
        $deadline = (Get-Date).AddSeconds($RuntimeSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            $live = Get-ThreatSnapshot
            Assert-NoNewInScope $live $scopeRoots $JobStart "runtime watch"
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
    Write-Host "All Defender threats/detections after runtime watch (informational):`n$(Format-Threats $afterRun)"
    Assert-NoNewInScope $afterRun $scopeRoots $JobStart "runtime watch"
}

Write-Host "Defender checks passed."
