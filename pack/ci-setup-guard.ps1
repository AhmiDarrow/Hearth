# Wait for leftover Inno Setup processes from a prior CI job, then stop only
# those whose executable path or command line is under this runner's work or
# temp directories. Never kill a Setup whose path and command line are outside
# those roots (for example a HearthSetup a person is running on this PC).
#
# Also used to kill the Setup process tree if the silent-install smoke times out,
# so a hung installer cannot hold SetupMutex and poison the next run.

[CmdletBinding()]
param(
    [ValidateSet("", "ClearLeftovers", "StopTree")]
    [string]$Action = "",
    [int]$WaitSeconds = 60,
    [int]$ProcessId = 0
)

$ErrorActionPreference = "Stop"

function Get-HearthRunnerRoots {
    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($raw in @($env:RUNNER_TEMP, $env:RUNNER_WORKSPACE, $env:GITHUB_WORKSPACE)) {
        if ([string]::IsNullOrWhiteSpace($raw)) { continue }
        try {
            $full = [System.IO.Path]::GetFullPath($raw.Trim())
        } catch {
            continue
        }
        $full = $full.TrimEnd('\', '/')
        # Never treat a drive root (C:\) as in-scope; that would match every process.
        if ($full -match '^[A-Za-z]:$') { continue }
        if ($full.Length -lt 4) { continue }
        $dup = $false
        foreach ($existing in $roots) {
            if ($existing.Equals($full, [StringComparison]::OrdinalIgnoreCase)) { $dup = $true; break }
        }
        if (-not $dup) { $roots.Add($full) }
    }
    return , $roots.ToArray()
}

function Get-NormalizedPathText([string]$Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return "" }
    return $Text.Replace('/', '\').Trim()
}

function Test-TextUnderRunnerRoots {
    param(
        [string]$Text,
        [string[]]$Roots
    )
    $n = (Get-NormalizedPathText $Text).ToLowerInvariant()
    if (-not $n) { return $false }
    foreach ($root in $Roots) {
        $r = (Get-NormalizedPathText $root).ToLowerInvariant().TrimEnd('\')
        if ([string]::IsNullOrWhiteSpace($r) -or $r -match '^[a-z]:$') { continue }
        if ($n -eq $r -or $n.StartsWith($r + '\')) { return $true }
        # Command lines embed the path mid-string: /DIR=C:\runner\_temp\... or quoted paths.
        $idx = 0
        while (($idx = $n.IndexOf($r, $idx)) -ge 0) {
            $beforeOk = ($idx -eq 0) -or ($n[$idx - 1] -match '[=\s,"'']')
            $afterIdx = $idx + $r.Length
            $afterOk = ($afterIdx -ge $n.Length) -or ($n[$afterIdx] -match '[\\/\s,"'']')
            if ($beforeOk -and $afterOk) { return $true }
            $idx = $idx + 1
        }
    }
    return $false
}

function Test-IsSetupImage {
    param(
        [string]$Name,
        [string]$Path
    )
    foreach ($c in @($Name, $Path)) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        $leaf = [System.IO.Path]::GetFileName($c.Trim().Trim('"'))
        # Current and leftover Hearth installers.
        if ($leaf -match '(?i)^HearthSetup.*\.exe$') { return $true }
        # Inno's extracted inner process is often named is-XXXX.tmp, or lives under that dir.
        if ($leaf -match '(?i)^is[-_].+\.tmp$') { return $true }
        $norm = Get-NormalizedPathText $c
        if ($norm -match '(?i)\\is[-_][^\\]+\.tmp(?:\\|$)') { return $true }
    }
    return $false
}

function Test-IsRunnerScopedSetup {
    param(
        [string]$Name,
        [string]$Path,
        [string]$CommandLine,
        [string[]]$Roots
    )
    if (-not $Roots -or $Roots.Count -eq 0) { return $false }
    if (-not (Test-IsSetupImage -Name $Name -Path $Path)) { return $false }
    # Kill only when the executable path OR the command line points under a runner root.
    # If both are missing or outside those roots, leave the process alone.
    if (Test-TextUnderRunnerRoots -Text $Path -Roots $Roots) { return $true }
    if (Test-TextUnderRunnerRoots -Text $CommandLine -Roots $Roots) { return $true }
    return $false
}

function Get-RunnerScopedSetupProcesses {
    $roots = Get-HearthRunnerRoots
    if ($roots.Count -eq 0) {
        Write-Warning "No RUNNER_TEMP/RUNNER_WORKSPACE/GITHUB_WORKSPACE roots are set; refusing to select any Setup process."
        return @()
    }
    Write-Host "Runner Setup-guard roots: $($roots -join '; ')"
    $procs = @()
    try {
        $procs = @(Get-CimInstance Win32_Process | Select-Object ProcessId, Name, ExecutablePath, CommandLine, ParentProcessId)
    } catch {
        Write-Warning "Could not enumerate processes: $_"
        return @()
    }
    $procs | Where-Object {
        Test-IsRunnerScopedSetup -Name $_.Name -Path $_.ExecutablePath -CommandLine $_.CommandLine -Roots $roots
    }
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    if ($ProcessId -le 4) { return }
    Write-Host "Stopping process tree PID $ProcessId (taskkill /T /F)"
    & taskkill.exe /PID $ProcessId /T /F 2>&1 | ForEach-Object { Write-Host $_ }
}

function Wait-ThenStop-RunnerScopedSetup {
    param([int]$WaitSeconds = 60)
    $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(0, $WaitSeconds))
    while ($true) {
        $found = @(Get-RunnerScopedSetupProcesses)
        if ($found.Count -eq 0) {
            Write-Host "No leftover runner-scoped HearthSetup/is-*.tmp processes."
            return
        }
        foreach ($p in $found) {
            Write-Host ("Waiting for leftover Setup PID {0} Name={1} Path={2} CommandLine={3}" -f $p.ProcessId, $p.Name, $p.ExecutablePath, $p.CommandLine)
        }
        if ([DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Seconds 2
    }
    $still = @(Get-RunnerScopedSetupProcesses)
    foreach ($p in $still) {
        Write-Host ("Terminating leftover runner-scoped Setup PID {0} Name={1} Path={2}" -f $p.ProcessId, $p.Name, $p.ExecutablePath)
        Stop-ProcessTree -ProcessId $p.ProcessId
    }
    Start-Sleep -Seconds 1
    $left = @(Get-RunnerScopedSetupProcesses)
    if ($left.Count -gt 0) {
        $desc = ($left | ForEach-Object { "$($_.ProcessId) $($_.Name)" }) -join ", "
        throw "Failed to clear runner-scoped Setup processes: $desc"
    }
    Write-Host "Cleared leftover runner-scoped Setup processes."
}

$invokedAsScript = $MyInvocation.InvocationName -ne '.' -and -not [string]::IsNullOrWhiteSpace($Action)
if ($invokedAsScript) {
    switch ($Action) {
        "ClearLeftovers" { Wait-ThenStop-RunnerScopedSetup -WaitSeconds $WaitSeconds }
        "StopTree" { Stop-ProcessTree -ProcessId $ProcessId }
    }
}
