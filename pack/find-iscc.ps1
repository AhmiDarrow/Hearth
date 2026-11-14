# Locate Inno Setup's ISCC.exe on this machine. Prefer the official install
# location, then Scoop, then PATH. Prints the path and exits 0, or throws.
$ErrorActionPreference = "Stop"
$candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:USERPROFILE "scoop\apps\inno-setup\current\ISCC.exe"),
    (Join-Path $env:USERPROFILE "scoop\shims\iscc.exe")
)
foreach ($path in $candidates) {
    if ($path -and (Test-Path -LiteralPath $path)) {
        Write-Output $path
        exit 0
    }
}
$cmd = Get-Command iscc, ISCC -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cmd -and $cmd.Source) {
    Write-Output $cmd.Source
    exit 0
}
throw "ISCC.exe (Inno Setup 6) was not found. Install it with Scoop (`scoop install inno-setup`) or the official installer."
