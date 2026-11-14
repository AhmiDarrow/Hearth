# Capture or restore Hearth launch args across a silent update.
# Capture must run in PrepareToInstall before taskkill, so an older
# Hearth that does not write relaunch-args.json still relaunches
# with the same --home/--port. Start must not invent a default profile.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Capture', 'Start')]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [string]$AppExe,
    [Parameter(Mandatory = $true)]
    [string]$ArgsFile,
    [string]$Updated = ''
)

$ErrorActionPreference = 'Stop'

function Get-ArgvFromCommandLine([string]$CommandLine, [string]$Exe) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return @() }
    $parts = [System.Collections.Generic.List[string]]::new()
    $current = New-Object System.Text.StringBuilder
    $inQuotes = $false
    foreach ($ch in $CommandLine.ToCharArray()) {
        if ($ch -eq '"') {
            $inQuotes = -not $inQuotes
            continue
        }
        if ((-not $inQuotes) -and [char]::IsWhiteSpace($ch)) {
            if ($current.Length -gt 0) {
                $parts.Add($current.ToString())
                [void]$current.Clear()
            }
            continue
        }
        [void]$current.Append($ch)
    }
    if ($current.Length -gt 0) { $parts.Add($current.ToString()) }
    $argv = $parts.ToArray()
    if ($argv.Length -eq 0) { return @() }
    $first = $argv[0].Trim('"')
    $exeNorm = [IO.Path]::GetFullPath($Exe)
    $skip = 0
    try {
        if ([string]::Equals([IO.Path]::GetFullPath($first), $exeNorm, [StringComparison]::OrdinalIgnoreCase)) { $skip = 1 }
    } catch {
        if ([string]::Equals($first, $Exe, [StringComparison]::OrdinalIgnoreCase) -or $first.EndsWith('Hearth.exe', [StringComparison]::OrdinalIgnoreCase)) { $skip = 1 }
    }
    $out = [System.Collections.Generic.List[string]]::new()
    $i = $skip
    while ($i -lt $argv.Length) {
        $a = $argv[$i]
        if ($a -eq '--updated' -or $a.StartsWith('--updated=')) {
            if ($a -eq '--updated' -and ($i + 1) -lt $argv.Length -and -not $argv[$i + 1].StartsWith('-')) { $i += 2; continue }
            $i += 1
            continue
        }
        [void]$out.Add($a)
        $i += 1
    }
    return $out.ToArray()
}

function Get-HomeFromArgv([string[]]$Argv) {
    for ($i = 0; $i -lt $Argv.Length; $i++) {
        if ($Argv[$i] -eq '--home' -and ($i + 1) -lt $Argv.Length) { return $Argv[$i + 1] }
        if ($Argv[$i].StartsWith('--home=')) { return $Argv[$i].Substring('--home='.Length) }
    }
    return ''
}

if ($Mode -eq 'Capture') {
    $instances = [System.Collections.Generic.List[object]]::new()
    $homes = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    if (Test-Path -LiteralPath $ArgsFile) {
        try {
            $existing = Get-Content -LiteralPath $ArgsFile -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($inst in @($existing.instances)) {
                $argv = @($inst.argv)
                if ($argv.Length -eq 0) { continue }
                $home = [string]$inst.home
                if (-not $home) { $home = Get-HomeFromArgv $argv }
                if ($home -and -not $homes.Add($home)) { continue }
                $instances.Add(@{ home = $home; argv = $argv })
            }
        } catch { }
    }
    $exeNorm = $null
    try { $exeNorm = [IO.Path]::GetFullPath($AppExe) } catch { $exeNorm = $AppExe }
    Get-CimInstance Win32_Process -Filter "Name='Hearth.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
        $path = [string]$_.ExecutablePath
        if (-not $path) { return }
        try { $path = [IO.Path]::GetFullPath($path) } catch { }
        if (-not [string]::Equals($path, $exeNorm, [StringComparison]::OrdinalIgnoreCase)) { return }
        $argv = Get-ArgvFromCommandLine ([string]$_.CommandLine) $AppExe
        $home = Get-HomeFromArgv $argv
        if ($home -and -not $homes.Add($home)) { return }
        if (-not $home -and $instances.Count -gt 0) { return }
        $instances.Add(@{ home = $home; argv = $argv })
    }
    $payload = @{ version = 1; instances = @($instances) }
    $dir = Split-Path -Parent $ArgsFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $payload | ConvertTo-Json -Depth 5 -Compress
    [IO.File]::WriteAllText($ArgsFile, $json, [Text.UTF8Encoding]::new($false))
    exit 0
}

if ($Mode -eq 'Start') {
    if (-not (Test-Path -LiteralPath $AppExe)) { throw "Hearth.exe not found: $AppExe" }
    if (-not (Test-Path -LiteralPath $ArgsFile)) {
        # Do not invent a default-profile instance after an update.
        exit 0
    }
    $data = Get-Content -LiteralPath $ArgsFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $work = Split-Path -Parent $AppExe
    $started = 0
    foreach ($inst in @($data.instances)) {
        $argv = @($inst.argv)
        $argList = @()
        if ($Updated) { $argList += @('--updated', $Updated) }
        $argList += $argv
        Start-Process -FilePath $AppExe -ArgumentList $argList -WorkingDirectory $work -WindowStyle Hidden | Out-Null
        $started += 1
    }
    if ($started -eq 0) { exit 0 }
    exit 0
}
