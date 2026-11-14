# Capture Hearth launch args across a silent update.
# Capture must run in PrepareToInstall before taskkill, so an older
# Hearth that does not write relaunch-args.json still relaunches
# with the same --home/--port. The installer relaunches via Inno [Run],
# not this script. Start remains for manual use and must not invent a
# default profile.
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

function Escape-JsonString([string]$Value) {
    if ($null -eq $Value) { return '' }
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq '\') { [void]$sb.Append('\\') }
        elseif ($ch -eq '"') { [void]$sb.Append('\"') }
        elseif ($ch -eq "`n") { [void]$sb.Append('\n') }
        elseif ($ch -eq "`r") { [void]$sb.Append('\r') }
        elseif ($ch -eq "`t") { [void]$sb.Append('\t') }
        else { [void]$sb.Append($ch) }
    }
    return $sb.ToString()
}

function ConvertTo-RelaunchJson($Instances) {
    # Build JSON by hand. Windows PowerShell 5.1 ConvertTo-Json unwraps a
    # one-element array, which would drop the instances list.
    $rows = New-Object System.Collections.Generic.List[string]
    foreach ($inst in @($Instances)) {
        $argvItems = New-Object System.Collections.Generic.List[string]
        foreach ($a in @($inst.argv)) {
            if ($null -eq $a) { continue }
            [void]$argvItems.Add('"' + (Escape-JsonString ([string]$a)) + '"')
        }
        $argvJson = '[' + ($argvItems -join ',') + ']'
        $homeJson = '"' + (Escape-JsonString ([string]$inst.home)) + '"'
        [void]$rows.Add('{ "home": ' + $homeJson + ', "argv": ' + $argvJson + ' }')
    }
    return '{ "version": 1, "instances": [' + ($rows -join ',') + '] }'
}

function Format-RelaunchCmdline($Argv) {
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($a in @($Argv)) {
        if ($null -eq $a) { continue }
        $s = [string]$a
        if ($s.Length -eq 0 -or $s.IndexOf(' ') -ge 0 -or $s.IndexOf("`t") -ge 0 -or $s.IndexOf('"') -ge 0) {
            $escaped = $s.Replace('"', '\"')
            if ($escaped.EndsWith('\')) { $escaped = $escaped + '\' }
            [void]$parts.Add('"' + $escaped + '"')
        } else {
            [void]$parts.Add($s)
        }
    }
    return ($parts -join ' ')
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
                # $HOME is read-only in Windows PowerShell. Do not assign it.
                $savedHome = [string]$inst.home
                if (-not $savedHome) { $savedHome = Get-HomeFromArgv $argv }
                if ($savedHome -and -not $homes.Add($savedHome)) { continue }
                $instances.Add(@{ home = $savedHome; argv = $argv })
            }
        } catch { }
    }
    $exeNorm = $null
    try { $exeNorm = [IO.Path]::GetFullPath($AppExe) } catch { $exeNorm = $AppExe }
    $appDir = ''
    try { $appDir = [IO.Path]::GetDirectoryName($exeNorm) } catch { $appDir = Split-Path -Parent $AppExe }
    $needle = ''
    if ($appDir) { $needle = $appDir.TrimEnd('\') + '\' }
    Get-CimInstance Win32_Process -Filter "Name='Hearth.exe'" -ErrorAction SilentlyContinue | ForEach-Object {
        $path = [string]$_.ExecutablePath
        $cmd = [string]$_.CommandLine
        $matched = $false
        if ($path) {
            try { $path = [IO.Path]::GetFullPath($path) } catch { }
            if ($exeNorm -and [string]::Equals($path, $exeNorm, [StringComparison]::OrdinalIgnoreCase)) { $matched = $true }
        }
        # ExecutablePath is often null for a process we did not start. Match
        # the install directory inside CommandLine so 0.3.8 is still captured.
        if (-not $matched -and $cmd) {
            if ($exeNorm -and ($cmd.IndexOf($exeNorm, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                $matched = $true
            } elseif ($needle -and ($cmd.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                $matched = $true
            }
        }
        if (-not $matched) { return }
        $argv = Get-ArgvFromCommandLine $cmd $AppExe
        $savedHome = Get-HomeFromArgv $argv
        if ($savedHome -and -not $homes.Add($savedHome)) { return }
        if (-not $savedHome -and $instances.Count -gt 0) { return }
        $instances.Add(@{ home = $savedHome; argv = $argv })
    }
    # An empty snapshot must not wipe a command line the app already saved.
    if ($instances.Count -eq 0) { exit 0 }
    $dir = Split-Path -Parent $ArgsFile
    if (-not $dir) { $dir = (Get-Location).Path }
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = ConvertTo-RelaunchJson $instances
    [IO.File]::WriteAllText($ArgsFile, $json, [Text.UTF8Encoding]::new($false))
    $cmdFile = Join-Path $dir 'relaunch-cmdline.txt'
    $line = Format-RelaunchCmdline @($instances[0].argv)
    if ($null -eq $line) { $line = '' }
    # Inno LoadStringFromFile reads ANSI, not UTF-8.
    [IO.File]::WriteAllText($cmdFile, [string]$line, [Text.Encoding]::Default)
    exit 0
}

if ($Mode -eq 'Start') {
    # The installer does not call Start. Silent relaunch is HiddenStart in the installer script.
    if (-not (Test-Path -LiteralPath $AppExe)) { throw "Hearth.exe not found: $AppExe" }
    if (-not (Test-Path -LiteralPath $ArgsFile)) {
        # Do not invent a default-profile instance after an update.
        exit 0
    }
    $data = Get-Content -LiteralPath $ArgsFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $work = Split-Path -Parent $AppExe
    foreach ($inst in @($data.instances)) {
        $pieces = New-Object System.Collections.Generic.List[string]
        if ($Updated) {
            [void]$pieces.Add('--updated')
            [void]$pieces.Add([string]$Updated)
        }
        foreach ($a in @($inst.argv)) {
            if ($null -ne $a) { [void]$pieces.Add([string]$a) }
        }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $AppExe
        $psi.Arguments = (Format-RelaunchCmdline $pieces.ToArray())
        $psi.WorkingDirectory = $work
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        [void][System.Diagnostics.Process]::Start($psi)
    }
    exit 0
}
