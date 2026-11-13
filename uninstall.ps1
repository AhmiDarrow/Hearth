#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$RemoveData,
    [switch]$FirewallOnly
)
$ErrorActionPreference = "Stop"
$TaskName = "Hearth"
$RuleTcp = "Hearth UI and file sharing"
$RuleUdp = "Hearth discovery"
$InstallDir = Join-Path $env:LOCALAPPDATA "Hearth"
$ShortcutPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Hearth.lnk"

function Test-IsAdmin {
    $ident = [Security.Principal.WindowsIdentity]::GetCurrent()
    $prin = New-Object Security.Principal.WindowsPrincipal($ident)
    return $prin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Remove-FirewallRules {
    foreach ($name in @($RuleTcp, $RuleUdp)) {
        Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    Write-Host "Removed Hearth firewall rules."
}

if ($FirewallOnly) {
    if (-not (Test-IsAdmin)) { throw "Administrator rights are required to remove firewall rules." }
    Remove-FirewallRules
    exit 0
}

Write-Host "Stopping Hearth if it is running..."
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "-m hearth" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
if (Test-Path $ShortcutPath) { Remove-Item $ShortcutPath -Force }

if (Test-IsAdmin) {
    Remove-FirewallRules
} else {
    Write-Host "Requesting Administrator approval to remove firewall rules..."
    $script = Join-Path $PSScriptRoot "uninstall.ps1"
    if (-not (Test-Path $script)) { $script = Join-Path $InstallDir "uninstall.ps1" }
    Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"", "-FirewallOnly"
    ) | Out-Null
}

if (Test-Path $InstallDir) {
    Write-Host "Removing $InstallDir"
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

if ($RemoveData) {
    $data = Join-Path $env:USERPROFILE ".hearth"
    if (Test-Path $data) {
        Remove-Item $data -Recurse -Force
        Write-Host "Removed saved hearth data at $data"
    }
} else {
    Write-Host "Left your group password and chat in $env:USERPROFILE\.hearth (use -RemoveData to delete)."
}

Write-Host "Hearth has been removed from this PC."
