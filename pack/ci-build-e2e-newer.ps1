# Build a signed throwaway installer (0.3.99-e2e) used only for the
# new -> newer CI auto-update test. Restores the release artifacts
# in dist\ afterwards so a later publish step still ships the real version.
param(
    [string]$ThrowawayVersion = "0.3.99-e2e",
    [string]$OutDir = "dist\e2e"
)
$ErrorActionPreference = "Stop"
if ($env:HEARTH_SIGN -ne "1") { throw "HEARTH_SIGN is not set; refuse to build an unsigned throwaway" }
if (-not $env:HEARTH_VERSION) { throw "HEARTH_VERSION is not set" }

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$stash = Join-Path $env:RUNNER_TEMP "hearth-e2e-release-stash"
if (Test-Path $stash) { Remove-Item $stash -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stash | Out-Null
foreach ($name in @("HearthSetup.exe", "Hearth.exe", "Hearth.cer", "SHA256SUMS.txt")) {
    $src = Join-Path "dist" $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $stash $name) -Force }
}

$init = Join-Path $root "hearth\__init__.py"
$origInit = Get-Content $init -Raw
$backupInit = Join-Path $stash "__init__.py"
Set-Content -Path $backupInit -Value $origInit -Encoding utf8NoBOM
try {
    $patched = [regex]::Replace($origInit, '__version__\s*=\s*"[^"]+"', "__version__ = `"$ThrowawayVersion`"")
    # .NET / PowerShell may add a BOM; write ASCII-ish utf8 without BOM via Python for safety.
    python -c "from pathlib import Path; p=Path(r'$init'); t=p.read_text(encoding='utf-8'); import re; p.write_text(re.sub(r'__version__\s*=\s*\"[^\"]+\"', '__version__ = \"$ThrowawayVersion\"', t), encoding='utf-8')"
    $got = python -c "from importlib import reload; import hearth; reload(hearth); from hearth import __version__; print(__version__, end='')"
    if ($got -ne $ThrowawayVersion) { throw "Failed to stamp throwaway version (got $got)" }
    $info = python -c "from hearth.update import Version; v=Version.parse('$ThrowawayVersion'); print(f'{v.major}.{v.minor}.{v.patch}', end='')"
    Write-Host "Building throwaway Hearth $ThrowawayVersion (file version $info)"

    python -m PyInstaller --noconfirm pack/hearth.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for throwaway build" }
    pwsh -NoProfile -File pack\sign-file.ps1 dist\Hearth\Hearth.exe

    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    $script = (Resolve-Path .\pack\sign-file.ps1).Path
    $sign = "pwsh.exe -NoProfile -ExecutionPolicy Bypass -File $script `$f"
    & $iscc /O"$OutDir" /DMyAppVersion=$ThrowawayVersion /DMyAppVersionInfo=$info /DSIGNBUILD "/Shearthsign=$sign" pack\hearth.iss
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed for throwaway build" }
    $setup = Join-Path $OutDir "HearthSetup.exe"
    if (-not (Test-Path $setup)) { throw "Throwaway HearthSetup.exe was not created" }
    Copy-Item pack\Hearth.cer (Join-Path $OutDir "Hearth.cer") -Force
    $hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
    "{0}  {1}" -f $hash, "HearthSetup.exe" | Set-Content -Path (Join-Path $OutDir "SHA256SUMS.txt") -Encoding ascii
    Get-Content (Join-Path $OutDir "SHA256SUMS.txt")
    Write-Host "Throwaway setup: $setup"
} finally {
    Copy-Item $backupInit $init -Force
    foreach ($name in @("HearthSetup.exe", "Hearth.exe", "Hearth.cer", "SHA256SUMS.txt")) {
        $src = Join-Path $stash $name
        if (Test-Path $src) { Copy-Item $src (Join-Path "dist" $name) -Force }
    }
    $restored = python -c "from importlib import reload; import hearth; reload(hearth); from hearth import __version__; print(__version__, end='')"
    Write-Host "Restored source version $restored and release artifacts in dist\"
}
