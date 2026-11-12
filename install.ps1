#Requires -Version 5.1
<#
.SYNOPSIS
  Install Hearth on Windows 11: venv, Start Menu shortcut, logon autostart, firewall rules.
#>
[CmdletBinding()]
param(
    [switch]$NoStart,
    [switch]$NoAutostart,
    [switch]$NoFirewall,
    [switch]$FirewallOnly
)

$ErrorActionPreference = "Stop"
$TaskName = "Hearth"
$RuleTcp = "Hearth UI and file sharing"
$RuleUdp = "Hearth discovery"
$HearthPortTcp = 7474
$HearthPortUdp = 45454
$InstallDir = Join-Path $env:LOCALAPPDATA "Hearth"
$SourceRoot = $PSScriptRoot
$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutPath = Join-Path $ShortcutDir "Hearth.lnk"

function Write-Step([string]$Message) {
    Write-Host "  $Message"
}

function Test-IsAdmin {
    $ident = [Security.Principal.WindowsIdentity]::GetCurrent()
    $prin = New-Object Security.Principal.WindowsPrincipal($ident)
    return $prin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Install-FirewallRules {
    Write-Host "Adding Windows Defender Firewall rules (Private profile)..."
    foreach ($name in @($RuleTcp, $RuleUdp)) {
        Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    New-NetFirewallRule -DisplayName $RuleTcp -Direction Inbound -Protocol TCP -LocalPort $HearthPortTcp -Action Allow -Profile Private -EdgeTraversalPolicy Allow | Out-Null
    New-NetFirewallRule -DisplayName $RuleUdp -Direction Inbound -Protocol UDP -LocalPort $HearthPortUdp -Action Allow -Profile Private | Out-Null
    Write-Host "  Allowed inbound TCP $HearthPortTcp and UDP $HearthPortUdp on Private networks."
}

if ($FirewallOnly) {
    if (-not (Test-IsAdmin)) {
        Write-Error "Firewall rules need an Administrator PowerShell. Right-click install.cmd and choose 'Run as administrator', or approve the UAC prompt."
        exit 1
    }
    Install-FirewallRules
    exit 0
}

function Get-PythonCommand {
    $candidates = @()
    foreach ($ver in @("3.13", "3.12", "3.11")) {
        $candidates += @{ File = "py"; Args = @("-$ver") }
    }
    $candidates += @{ File = "py"; Args = @("-3") }
    $candidates += @{ File = "python" }
    $candidates += @{ File = "python3" }

    $pf = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python312",
        "$env:ProgramFiles\Python311",
        "$env:ProgramFiles\Python313",
        "${env:ProgramFiles(x86)}\Python312",
        "${env:ProgramFiles(x86)}\Python311"
    )
    foreach ($root in $pf) {
        if (Test-Path $root) {
            Get-ChildItem -Path $root -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 6 |
                ForEach-Object { $candidates += @{ File = $_.FullName } }
        }
    }

    foreach ($c in $candidates) {
        $file = $c.File
        $pyLaunchArgs = @()
        if ($c.ContainsKey("Args") -and $c.Args) { $pyLaunchArgs = $c.Args }
        try {
            $cmd = Get-Command $file -ErrorAction SilentlyContinue
            if (-not $cmd -and -not (Test-Path $file)) { continue }
            $allArgs = $pyLaunchArgs + @("-c", "import sys; v=sys.version_info; print(('%d.%d'%v[:2]) if v>=(3,11) else 'OLD')")
            $out = & $file @allArgs 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and ($out.ToString().Trim() -ne "OLD")) {
                return @{ File = $file; Args = $pyLaunchArgs; Version = $out.ToString().Trim() }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Install-PythonWithWinget {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { return $false }
    Write-Host "Python 3.11+ was not found. Installing Python 3.12 with winget..."
    & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { return $false }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    return $true
}

Write-Host ""
Write-Host "Hearth setup for Windows 11"
Write-Host "---------------------------"

if (-not (Test-Path (Join-Path $SourceRoot "hearth\__main__.py"))) {
    Write-Error "This script must live next to the hearth source folder. Re-copy the repo and run install.cmd again."
    exit 1
}

$python = Get-PythonCommand
if (-not $python) {
    if (Install-PythonWithWinget) {
        $python = Get-PythonCommand
    }
}
if (-not $python) {
    Write-Host ""
    Write-Host "Hearth needs Python 3.11 or later on this PC."
    Write-Host "Install it, then run install.cmd again:"
    Write-Host "  winget install -e --id Python.Python.3.12"
    Write-Host "  or download https://www.python.org/downloads/"
    Write-Host "Tick 'Add python.exe to PATH' if you use the python.org installer."
    exit 1
}
Write-Step "Using Python $($python.Version) ($($python.File))"

Write-Host "Installing to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "logs") | Out-Null

$copyNames = @(
    "hearth", "requirements.txt", "pyproject.toml", "README.md", "LICENSE",
    "run.ps1", "run.cmd", "install.ps1", "install.cmd", "uninstall.ps1", "uninstall.cmd"
)
foreach ($name in $copyNames) {
    $src = Join-Path $SourceRoot $name
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $InstallDir $name) -Recurse -Force
    }
}

$venvPython = Join-Path $InstallDir "venv\Scripts\python.exe"
$venvPythonw = Join-Path $InstallDir "venv\Scripts\pythonw.exe"
$pyArgs = @()
if ($python.ContainsKey("Args") -and $python.Args) { $pyArgs = $python.Args }

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating a virtual environment..."
    & $python.File @($pyArgs + @("-m", "venv", (Join-Path $InstallDir "venv")))
    if ($LASTEXITCODE -ne 0) { throw "Could not create a virtual environment." }
}

Write-Host "Installing Python packages..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }
if (Test-Path (Join-Path $InstallDir "pyproject.toml")) {
    & $venvPython -m pip install -e $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "Package install failed." }
}

# Start Menu shortcut opens the UI (and reuses a running instance).
Write-Host "Adding a Start Menu shortcut..."
New-Item -ItemType Directory -Force -Path $ShortcutDir | Out-Null
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($ShortcutPath)
$sc.TargetPath = $venvPythonw
$sc.Arguments = "-m hearth"
$sc.WorkingDirectory = $InstallDir
$sc.WindowStyle = 7
$sc.Description = "Hearth — home file sharing and chat"
$sc.Save()

if (-not $NoAutostart) {
    Write-Host "Registering a logon task so Hearth starts with Windows..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $venvPythonw -Argument "-m hearth --no-browser" -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Start Hearth in the background after you sign in." -Force | Out-Null
    Write-Step "Task Scheduler: $TaskName (at logon, this user)"
}

if (-not $NoFirewall) {
    if (Test-IsAdmin) {
        Install-FirewallRules
    } else {
        Write-Host "Requesting Administrator approval for firewall rules..."
        $elev = Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", "`"$InstallDir\install.ps1`"",
            "-FirewallOnly"
        )
        if ($elev.ExitCode -ne 0) {
            Write-Host ""
            Write-Host "Firewall rules were not added automatically. Other PCs may not see this one until you allow Hearth."
            Write-Host "Re-run install.cmd as administrator, or add these inbound Private rules in Windows Security:"
            Write-Host "  TCP $HearthPortTcp   (web UI, folder browse, chat)"
            Write-Host "  UDP $HearthPortUdp  (discovery beacons)"
        }
    }
}

if (-not $NoStart) {
    Write-Host "Starting Hearth..."
    Start-Process -FilePath $venvPythonw -ArgumentList "-m hearth" -WorkingDirectory $InstallDir
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "Done. On this PC:"
Write-Host "  1. Open http://127.0.0.1:$HearthPortTcp  (it should have opened already)"
Write-Host "  2. Name the PC and click Light the hearth — or Join with an invite from another PC"
Write-Host "  3. Click Share a folder and pick Documents, Pictures, or any folder"
Write-Host "  4. Copy the invite and paste it on the next Windows 11 PC, then run install.cmd there too"
Write-Host ""
Write-Host "Hearth starts when you sign in, and is pinned in the Start Menu as 'Hearth'."
Write-Host "To remove it later, run uninstall.cmd."
Write-Host ""
