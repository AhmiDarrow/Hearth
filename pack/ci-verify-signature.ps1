# Verify release EXEs are Authenticode-signed with the Hearth publisher
# thumbprint. Self-signed certs are expected to report NotTrusted or
# UnknownError; that is not a failure. NotSigned, HashMismatch, or a
# missing/wrong thumbprint fails the build.
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Path,
    [string]$Thumbprint = "C7B807D542B3BA7207AABED960F556B4DC85C02F"
)

$ErrorActionPreference = "Stop"
$want = ($Thumbprint -replace "[^0-9A-Fa-f]", "").ToUpperInvariant()

function Get-NormalizedThumbprint([string]$value) {
    return ($value -replace "[^0-9A-Fa-f]", "").ToUpperInvariant()
}

$failed = $false
foreach ($item in $Path) {
    $full = (Resolve-Path $item).Path
    Write-Host "==== Get-AuthenticodeSignature $full ===="
    $sig = Get-AuthenticodeSignature -FilePath $full
    $status = [string]$sig.Status
    $got = ""
    if ($sig.SignerCertificate) {
        $got = Get-NormalizedThumbprint $sig.SignerCertificate.Thumbprint
    }
    Write-Host "Status=$status"
    Write-Host "Thumbprint=$got"
    if ($sig.StatusMessage) {
        Write-Host "StatusMessage=$($sig.StatusMessage)"
    }
    if (-not $got) {
        Write-Error "FAIL: $full is not Authenticode-signed (no signer certificate, Status=$status)"
        $failed = $true
        continue
    }
    if ($got -ne $want) {
        Write-Error "FAIL: $full thumbprint $got does not match $want (Status=$status)"
        $failed = $true
        continue
    }
    if ($status -eq "NotSigned" -or $status -eq "HashMismatch") {
        Write-Error "FAIL: $full signature is not valid (Status=$status)"
        $failed = $true
        continue
    }
    Write-Host "OK: $full is signed with the Hearth thumbprint (Status=$status)"
}

if ($failed) {
    throw "Authenticode gate failed: one or more assets are unsigned or signed by the wrong certificate"
}

Write-Host "Authenticode gate passed."
