# Signs a Hearth.exe built on the owner's PC and builds its signed HearthSetup.exe.
#
#   pack\ci-package.ps1 -Exe dist\upload\Hearth_1.0.0_unsigned.exe -Version 1.0.0 -OutDir dist
#
# Leaves dist\Hearth\Hearth.exe (signed, what Setup installs and the checks run) and, in
# OutDir, HearthSetup.exe, Hearth.cer and SHA256SUMS.txt. The program's own version must be
# Version: a Setup that says one version and installs another would update forever.
param(
  [Parameter(Mandatory = $true)][string]$Exe,
  [Parameter(Mandatory = $true)][string]$Version,
  [Parameter(Mandatory = $true)][string]$OutDir
)

$ErrorActionPreference = "Stop"
if ($env:HEARTH_SIGN -ne "1") { throw "Signing is not configured (HEARTH_SIGN); refusing to package unsigned." }
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$built = (Get-Item $Exe).VersionInfo.ProductVersion
if ($built -ne $Version) { throw "$Exe is version '$built', expected '$Version'" }
$info = ($Version -split "[-+]")[0]
if ($info -notmatch '^\d+\.\d+\.\d+$') { throw "Version '$Version' does not start with major.minor.patch" }

$stage = Join-Path $root "dist\Hearth"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item $Exe (Join-Path $stage "Hearth.exe")
& (Join-Path $PSScriptRoot "sign-file.ps1") (Join-Path $stage "Hearth.exe")

$iscc = if ($env:HEARTH_ISCC) { $env:HEARTH_ISCC } else { (& (Join-Path $PSScriptRoot "find-iscc.ps1")).Trim() }
if (-not $iscc -or -not (Test-Path $iscc)) { throw "ISCC.exe was not found" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$out = (Resolve-Path $OutDir).Path
# Inno treats " in a SignTool command as its own quoting: a space-free absolute path, and
# Inno quotes $f itself.
$script = (Resolve-Path (Join-Path $PSScriptRoot "sign-file.ps1")).Path
$sign = "pwsh.exe -NoProfile -ExecutionPolicy Bypass -File $script `$f"
& $iscc /O"$out" /DMyAppVersion=$Version /DMyAppVersionInfo=$info /DSIGNBUILD "/Shearthsign=$sign" pack\hearth.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
$setup = Join-Path $out "HearthSetup.exe"
if (-not (Test-Path $setup)) { throw "HearthSetup.exe was not created" }

Copy-Item pack\Hearth.cer (Join-Path $out "Hearth.cer") -Force
Copy-Item (Join-Path $stage "Hearth.exe") (Join-Path $out "Hearth.exe") -Force
$lines = foreach ($name in @("HearthSetup.exe", "Hearth.exe", "Hearth.cer")) {
  $hash = (Get-FileHash (Join-Path $out $name) -Algorithm SHA256).Hash.ToLower()
  "{0}  {1}" -f $hash, $name
}
$lines | Set-Content -Path (Join-Path $out "SHA256SUMS.txt") -Encoding ascii
Get-Content (Join-Path $out "SHA256SUMS.txt")
Get-Item $setup | Format-List Name, Length, LastWriteTime
