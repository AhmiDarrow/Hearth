# Read-only snapshot and verify of the interactive user's Hearth install.
# This script never stops a process and never deletes a file.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Snapshot', 'Verify')]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [string]$SnapshotPath
)

$ErrorActionPreference = 'Stop'

function Get-UserExePath {
    if (-not $env:LOCALAPPDATA) { return '' }
    return (Join-Path $env:LOCALAPPDATA 'Programs\Hearth\Hearth.exe')
}

function Get-InstallLocation {
    $subkey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8E4C1F2A-9B3D-4A71-8E6C-2F5D9A1B0C44}_is1'
    try {
        $item = Get-ItemProperty -Path $subkey -Name InstallLocation -ErrorAction Stop
        return [string]$item.InstallLocation
    } catch {
        return ''
    }
}

function Get-ShortcutLines {
    $sh = New-Object -ComObject WScript.Shell
    $paths = @(
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Hearth.lnk'),
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Hearth.lnk'),
        (Join-Path $env:USERPROFILE 'Desktop\Hearth.lnk'),
        (Join-Path $env:USERPROFILE 'OneDrive\Desktop\Hearth.lnk')
    )
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($lnk in $paths) {
        if (Test-Path -LiteralPath $lnk) {
            $sc = $sh.CreateShortcut($lnk)
            $target = [string]$sc.TargetPath
            $shortcutArgs = [string]$sc.Arguments
            $lines.Add("$lnk|$target|$shortcutArgs")
        } else {
            $lines.Add("$lnk||")
        }
    }
    return ($lines -join "`n")
}

function Get-UserPids([string]$ExePath) {
    if (-not $ExePath) { return '' }
    $ids = New-Object System.Collections.Generic.List[string]
    $rows = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'Hearth.exe' -and $_.ExecutablePath -and
        [string]::Equals($_.ExecutablePath, $ExePath, [System.StringComparison]::OrdinalIgnoreCase)
    }
    foreach ($row in @($rows)) {
        if ($null -ne $row.ProcessId) { $ids.Add([string]$row.ProcessId) }
    }
    return ($ids -join ',')
}

function Get-UserFacts {
    $exe = Get-UserExePath
    $exeExists = $false
    $length = [int64]0
    $mtime = ''
    $sha = ''
    $dirExists = $false
    if ($exe) {
        $dirExists = Test-Path -LiteralPath (Split-Path -Parent $exe)
        if (Test-Path -LiteralPath $exe) {
            $exeExists = $true
            $item = Get-Item -LiteralPath $exe
            $length = [int64]$item.Length
            $mtime = $item.LastWriteTimeUtc.ToString('o')
            $sha = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
        }
    }
    return @{
        exe = [string]$exe
        exeExists = [bool]$exeExists
        length = $length
        mtime = [string]$mtime
        sha256 = [string]$sha
        dirExists = [bool]$dirExists
        pids = (Get-UserPids $exe)
        installLocation = (Get-InstallLocation)
        shortcuts = (Get-ShortcutLines)
    }
}

function Assert-StillThere($Before, $After) {
    if (-not [bool]$Before.exeExists -and -not [bool]$Before.dirExists) {
        if ([bool]$After.exeExists -or [bool]$After.dirExists) {
            throw 'User Hearth install appeared during the job'
        }
    } else {
        if ([bool]$Before.exeExists) {
            if (-not [bool]$After.exeExists) { throw 'User Hearth.exe disappeared' }
            if ([int64]$After.length -ne [int64]$Before.length) { throw 'User Hearth.exe length changed' }
            if (-not [string]::Equals([string]$After.sha256, [string]$Before.sha256, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'User Hearth.exe hash changed'
            }
            $oldTime = [datetime]::Parse([string]$Before.mtime).ToUniversalTime()
            $newTime = [datetime]::Parse([string]$After.mtime).ToUniversalTime()
            $delta = [math]::Abs(($newTime - $oldTime).TotalSeconds)
            if ($delta -gt 1) { throw 'User Hearth.exe timestamp changed' }
        } elseif ([bool]$After.exeExists) {
            throw 'User Hearth.exe appeared during the job'
        }
        if ([bool]$Before.dirExists -ne [bool]$After.dirExists) {
            throw 'User Hearth directory changed'
        }
    }
    if ([string]$Before.installLocation -ne [string]$After.installLocation) {
        throw 'User Hearth uninstall location changed'
    }
    if ([string]$Before.shortcuts -ne [string]$After.shortcuts) {
        throw 'User Hearth shortcuts changed'
    }
    $recorded = @()
    if ($Before.pids) { $recorded = [string]$Before.pids -split ',' }
    foreach ($textId in $recorded) {
        if (-not $textId) { continue }
        $procIdNum = [int]$textId
        $alive = Get-CimInstance Win32_Process -Filter "ProcessId=$procIdNum" -ErrorAction SilentlyContinue
        if (-not $alive -or $alive.Name -ne 'Hearth.exe') {
            throw "User Hearth process $procIdNum is gone or is no longer Hearth.exe"
        }
    }
}

if ($Mode -eq 'Snapshot') {
    $parent = Split-Path -Parent $SnapshotPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $facts = Get-UserFacts
    $json = $facts | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($SnapshotPath, $json)
    Write-Host "Snapshotted user Hearth exe=$($facts.exe) exists=$($facts.exeExists) pids=$($facts.pids)"
    exit 0
}

if (-not (Test-Path -LiteralPath $SnapshotPath)) {
    throw "User-install snapshot is missing: $SnapshotPath"
}
$raw = [System.IO.File]::ReadAllText($SnapshotPath)
$before = $raw | ConvertFrom-Json
$after = Get-UserFacts
Assert-StillThere $before $after
Write-Host "User Hearth install unchanged exe=$($after.exe) pids=$($before.pids)"
exit 0
