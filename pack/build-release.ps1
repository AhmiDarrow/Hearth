# Builds what a Hearth release is made from, on the PC that has RemedyUI beside this
# repository, and (with -Upload) puts it on a draft release for the "Windows installer"
# workflow to sign, build Setup from, install and update.
#
#   pwsh pack\build-release.ps1            build and test: dist\upload\Hearth_<version>_unsigned.exe
#                                          and the throwaway newer one the update check installs
#   pwsh pack\build-release.ps1 -Upload    also create the draft v<version> with both on it
#
# The version is rust\Cargo.toml's. The newer program is the same source at the next minor
# version with "-e2e" (1.0.0 -> 1.1.0-e2e); it never leaves the checks.
param(
  [switch]$Upload,
  [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$rust = Join-Path $root "rust"
$manifest = Join-Path $rust "Cargo.toml"
$out = Join-Path $root "dist\upload"

function Get-CargoVersion {
  $line = Select-String -Path $manifest -Pattern '^version = "(.+)"' | Select-Object -First 1
  if (-not $line) { throw "no version in $manifest" }
  return $line.Matches[0].Groups[1].Value
}

function Build-Hearth([string]$version) {
  Push-Location $rust
  try {
    cargo build --release -p hearth
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed for $version" }
  } finally { Pop-Location }
  $exe = Join-Path $rust "target\release\Hearth.exe"
  $built = (Get-Item $exe).VersionInfo.ProductVersion
  if ($built -ne $version) { throw "built Hearth.exe is '$built', expected '$version'" }
  $dest = Join-Path $out "Hearth_${version}_unsigned.exe"
  Copy-Item $exe $dest -Force
  return $dest
}

$version = Get-CargoVersion
if ($version -notmatch '^(\d+)\.(\d+)\.(\d+)$') { throw "release version must be major.minor.patch, got $version" }
$newer = "{0}.{1}.0-e2e" -f $Matches[1], ([int]$Matches[2] + 1)
Write-Host "Hearth $version (update check installs $newer)"
New-Item -ItemType Directory -Force -Path $out | Out-Null

if (-not $SkipTests) {
  Push-Location $rust
  try {
    cargo test --workspace
    if ($LASTEXITCODE -ne 0) { throw "tests failed; nothing was built" }
  } finally { Pop-Location }
}

$release = Build-Hearth $version
# The newer program: the version line changed for one build, then put back.
$original = Get-Content $manifest -Raw
$lock = Join-Path $rust "Cargo.lock"
$originalLock = Get-Content $lock -Raw
try {
  ($original -replace ('(?m)^version = "' + [regex]::Escape($version) + '"'), ('version = "' + $newer + '"')) |
    Set-Content -Path $manifest -NoNewline
  $newerExe = Build-Hearth $newer
} finally {
  Set-Content -Path $manifest -Value $original -NoNewline
  Set-Content -Path $lock -Value $originalLock -NoNewline
}
Get-Item $release, $newerExe | Format-Table Name, Length

if ($Upload) {
  $tag = "v$version"
  $sha = (git -C $root rev-parse HEAD).Trim()
  gh release view $tag --repo AhmiDarrow/Hearth *> $null
  if ($LASTEXITCODE -ne 0) {
    gh release create $tag --repo AhmiDarrow/Hearth --draft --target $sha --title "Hearth $tag" --generate-notes
    if ($LASTEXITCODE -ne 0) { throw "could not create the draft $tag" }
  }
  # The Linux package, built in Linux by pack/build-linux.sh into the same folder.
  $linux = @("Hearth-$version-linux-x86_64.tar.gz", "Hearth_${version}_linux-x86_64") | ForEach-Object { Join-Path $out $_ }
  foreach ($f in $linux) { if (-not (Test-Path $f)) { throw "$f is missing: run pack/build-linux.sh in Linux first" } }
  gh release upload $tag $release $newerExe @linux --repo AhmiDarrow/Hearth --clobber
  if ($LASTEXITCODE -ne 0) { throw "upload failed" }
  Write-Host "Draft $tag has both programs. Run the Windows installer workflow with tag $tag."
}
