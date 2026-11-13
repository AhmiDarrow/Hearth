param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$FilePath
)
$ErrorActionPreference = "Stop"
if (-not $env:HEARTH_PFX_PATH -or -not $env:WINDOWS_CERT_PASSWORD) {
  throw "Signing environment is not configured."
}
if (-not (Test-Path $FilePath)) {
  throw "File to sign was not found: $FilePath"
}
$signtool = @(
  Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue
  Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\App Certification Kit\signtool.exe" -ErrorAction SilentlyContinue
) | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) {
  throw "signtool.exe not found. The Windows SDK is required to sign."
}
& $signtool.FullName sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /f $env:HEARTH_PFX_PATH /p $env:WINDOWS_CERT_PASSWORD $FilePath
if ($LASTEXITCODE -ne 0) {
  throw "signtool failed for $FilePath"
}
