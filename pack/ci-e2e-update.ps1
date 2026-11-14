# End-to-end auto-update test on windows-latest.
# 1) previous release -> new (installed updater, fake GitHub API)
# 2) new -> throwaway newer (fixed updater via HEARTH_RELEASES_URL)
# Evidence (logs, JSON, screenshots) is written to -EvidenceDir.
param(
    [Parameter(Mandatory = $true)]
    [string]$NewSetup,
    [Parameter(Mandatory = $true)]
    [string]$NewSums,
    [Parameter(Mandatory = $true)]
    [string]$NewVersion,
    [string]$NewerSetup = "",
    [string]$NewerSums = "",
    [string]$NewerVersion = "",
    [string]$EvidenceDir = "",
    [string]$RepoRoot = "",
    [string[]]$PreviousTags = @("v0.3.6", "v0.3.4"),
    [switch]$SkipPrevious,
    [switch]$RequirePrevious
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $env:RUNNER_TEMP "hearth-e2e-evidence" }
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$transcript = Join-Path $EvidenceDir "transcript.txt"
Start-Transcript -Path $transcript -Force | Out-Null

function Write-Log([string]$Message) {
    $stamp = (Get-Date).ToString("s")
    Write-Host "[$stamp] $Message"
}

function Save-Screenshot([string]$Path) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $dir = Split-Path $Path
        if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        Write-Log "Screenshot $Path"
    } catch {
        Write-Log "Screenshot failed: $_"
    }
}

function Assert-IsolatedDir([string]$Dir) {
    $full = [System.IO.Path]::GetFullPath($Dir).TrimEnd('\')
    $banned = @()
    if ($env:LOCALAPPDATA) { $banned += (Join-Path $env:LOCALAPPDATA "Programs\Hearth") }
    if ($env:ProgramFiles) { $banned += (Join-Path $env:ProgramFiles "Hearth") }
    if ($env:USERPROFILE) { $banned += (Join-Path $env:USERPROFILE ".hearth") }
    foreach ($item in $banned) {
        if (-not $item) { continue }
        $other = [System.IO.Path]::GetFullPath($item).TrimEnd('\')
        if ($full.Equals($other, [System.StringComparison]::OrdinalIgnoreCase)) { throw "refusing to use $Dir" }
        if ($full.StartsWith($other + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "refusing to use $Dir" }
        if ($other.StartsWith($full + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "refusing to use $Dir" }
    }
}

function Get-InstalledHearth([string]$DirHint) {
    $candidate = Join-Path $DirHint "Hearth.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return $null
}

function Stop-HearthUnder([string]$Root) {
    if (-not $Root) { return }
    Assert-IsolatedDir $Root
    $prefix = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    try {
        $rows = @(Get-CimInstance Win32_Process -Filter "Name = 'Hearth.exe'" -ErrorAction Stop)
    } catch {
        Write-Log "could not list Hearth processes: $_"
        return
    }
    foreach ($row in $rows) {
        if (-not $row) { continue }
        $exePath = [string]$row.ExecutablePath
        if (-not $exePath) { continue }
        if ($exePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $procIdNum = $row.ProcessId
            Write-Log "taskkill /PID $procIdNum under $Root"
            & taskkill.exe /PID $procIdNum /T /F | Out-Null
        }
    }
}

function Install-Setup([string]$Setup, [string]$Dir) {
    Assert-IsolatedDir $Dir
    if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    Write-Log "Silent install $Setup -> $Dir"
    $p = Start-Process -FilePath $Setup -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/CURRENTUSER", "/DIR=$Dir") -Wait -PassThru -WindowStyle Hidden
    if ($p.ExitCode -ne 0) { throw "Silent install failed with exit $($p.ExitCode): $Setup" }
    $exe = Get-InstalledHearth $Dir
    if (-not $exe) { throw "Hearth.exe not found after installing $Setup into $Dir" }
    return $exe
}

function Extract-Setup([string]$Setup, [string]$Dir) {
    Assert-IsolatedDir $Dir
    $refuse = "extracting previous Setup; refusing to run it because it closes every Hearth.exe"
    Write-Log $refuse
    if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $staging = Join-Path (Split-Path $Dir) ((Split-Path $Dir -Leaf) + "-extract")
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    $extracted = $false
    $innounp = Get-Command innounp -ErrorAction SilentlyContinue
    if ($innounp) {
        & $innounp.Source -x -b -y -q "-d$staging" $Setup
        if ($LASTEXITCODE -eq 0 -and (Get-ChildItem -LiteralPath $staging -Recurse -Filter "Hearth.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            $extracted = $true
        }
    }
    if (-not $extracted) {
        $seven = Get-Command 7z -ErrorAction SilentlyContinue
        if ($seven) {
            & $seven.Source x -y "-o$staging" $Setup
            if ($LASTEXITCODE -eq 0 -and (Get-ChildItem -LiteralPath $staging -Recurse -Filter "Hearth.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                $extracted = $true
            }
        }
    }
    if (-not $extracted) {
        $inno = Get-Command innoextract -ErrorAction SilentlyContinue
        if ($inno) {
            & $inno.Source -d $staging $Setup
            if ($LASTEXITCODE -eq 0 -and (Get-ChildItem -LiteralPath $staging -Recurse -Filter "Hearth.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                $extracted = $true
            }
        }
    }
    if ($extracted) {
        $found = Get-ChildItem -LiteralPath $staging -Recurse -Filter "Hearth.exe" -File -ErrorAction SilentlyContinue |
            Sort-Object { $_.FullName.Length } |
            Select-Object -First 1
        if ($found) {
            Get-ChildItem -LiteralPath $found.DirectoryName -Force | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dir $_.Name) -Recurse -Force
            }
        }
    }
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue }
    $exe = Join-Path $Dir "Hearth.exe"
    if (Test-Path -LiteralPath $exe) { return $exe }
    throw $refuse
}

function Uninstall-Hearth([string]$Dir) {
    Assert-IsolatedDir $Dir
    Stop-HearthUnder $Dir
    $candidate = Join-Path $Dir "unins000.exe"
    if (Test-Path -LiteralPath $candidate) {
        Write-Log "Uninstall $candidate"
        $p = Start-Process -FilePath $candidate -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru -WindowStyle Hidden
        Write-Log "Uninstaller exit $($p.ExitCode)"
        return
    }
    Write-Log "No uninstaller in $Dir"
}

function Wait-ApiState([int]$Port, [int]$TimeoutSec, [string]$OutFile) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $url = "http://127.0.0.1:$Port/api/state"
    $last = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
            if ($resp.StatusCode -eq 200) {
                $resp.Content | Set-Content -Path $OutFile -Encoding utf8
                return $resp.Content | ConvertFrom-Json
            }
        } catch {
            $last = $_
        }
        Start-Sleep -Milliseconds 400
    }
    throw "Timed out waiting for $url ($last)"
}

function Invoke-UpdateCheck([int]$Port, [bool]$Install, [string]$OutFile) {
    $url = "http://127.0.0.1:$Port/api/updates/check"
    $body = @{ install = $Install } | ConvertTo-Json
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Method POST -Uri $url -ContentType "application/json" -Body $body -TimeoutSec 180
        $resp.Content | Set-Content -Path $OutFile -Encoding utf8
        return @{ Status = [int]$resp.StatusCode; Body = ($resp.Content | ConvertFrom-Json); Raw = $resp.Content }
    } catch {
        $ex = $_.Exception
        $raw = $null
        if ($ex.Response) {
            try {
                $reader = New-Object System.IO.StreamReader($ex.Response.GetResponseStream())
                $raw = $reader.ReadToEnd()
            } catch {}
        }
        if (-not $raw) { $raw = "$_" }
        $raw | Set-Content -Path $OutFile -Encoding utf8
        $status = 0
        try { $status = [int]$ex.Response.StatusCode } catch {}
        return @{ Status = $status; Body = $null; Raw = $raw; Error = "$_" }
    }
}

function Start-Hearth([string]$Exe, [string]$HomeDir, [int]$Port, [hashtable]$ExtraEnv) {
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
    Stop-HearthUnder (Split-Path -Parent $Exe)
    Start-Sleep -Milliseconds 300
    $env:HEARTH_UPDATE_DIR = Split-Path -Parent $Exe
    $env:HEARTH_HOME = $HomeDir
    $env:HEARTH_PORT = "$Port"
    Remove-Item Env:HEARTH_NO_UPDATE -ErrorAction SilentlyContinue
    Remove-Item Env:HEARTH_RELEASES_URL -ErrorAction SilentlyContinue
    Remove-Item Env:HEARTH_UPDATE_SKIP_OPEN -ErrorAction SilentlyContinue
    if ($ExtraEnv) {
        foreach ($k in $ExtraEnv.Keys) {
            Set-Item -Path "Env:$k" -Value $ExtraEnv[$k]
        }
    }
    $env:HEARTH_CI = "1"
    Write-Log "Starting $Exe --no-desktop --no-browser --port $Port --home $HomeDir --host 127.0.0.1"
    $proc = Start-Process -FilePath $Exe `
      -WindowStyle Hidden `
        -ArgumentList @("--no-desktop", "--no-browser", "--port", "$Port", "--home", $HomeDir, "--host", "127.0.0.1") `
        -WorkingDirectory (Split-Path $Exe) `
        -PassThru `
        -RedirectStandardOutput (Join-Path $HomeDir "stdout.log") `
        -RedirectStandardError (Join-Path $HomeDir "stderr.log")
    return $proc
}

function Stop-Hearth([System.Diagnostics.Process]$Proc, [string]$Root) {
    try { Invoke-WebRequest -UseBasicParsing -Method POST -Uri "http://127.0.0.1:$($env:HEARTH_PORT)/api/quit" -TimeoutSec 5 | Out-Null } catch {}
    Start-Sleep -Milliseconds 400
    if ($Root) { Stop-HearthUnder $Root }
    if ($Proc -and $Proc.Id) {
        & taskkill.exe /PID $Proc.Id /T /F | Out-Null
    }
}

function Start-HttpReleases([string]$Setup, [string]$Sums, [string]$Tag, [int]$Port, [string]$Dir) {
    $pidFile = Join-Path $Dir "server.pid"
    $outLog = Join-Path $Dir "server.out"
    $errLog = Join-Path $Dir "server.err"
    $script = Join-Path $RepoRoot "pack\ci_e2e_releases.py"
    $p = Start-Process -FilePath "python" -WindowStyle Hidden -ArgumentList @(
        $script, "--setup", $Setup, "--sums", $Sums, "--tag", $Tag,
        "--host", "127.0.0.1", "--port", "$Port", "--pid-file", $pidFile
    ) -PassThru -RedirectStandardOutput $outLog -RedirectStandardError $errLog -NoNewWindow
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $outLog) {
            $line = Select-String -Path $outLog -Pattern "E2E_RELEASES_URL=" | Select-Object -Last 1
            if ($line) {
                $url = ($line.Line -replace "^E2E_RELEASES_URL=", "").Trim()
                return @{ Process = $p; Url = $url; OutLog = $outLog; ErrLog = $errLog }
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Fake releases server did not start. out=$(Get-Content $outLog -ErrorAction SilentlyContinue) err=$(Get-Content $errLog -ErrorAction SilentlyContinue)"
}

function Start-HttpsGitHubStandIn([string]$Setup, [string]$Sums, [string]$Tag, [string]$Dir) {
    # Self-signed api.github.com cert in LocalMachine Root, plus hosts file, so
    # an already-shipped updater (no URL override) talks to this process.
    $cert = New-SelfSignedCertificate -DnsName "api.github.com" -FriendlyName "Hearth E2E api.github.com" `
        -CertStoreLocation "Cert:\LocalMachine\My" -NotAfter (Get-Date).AddDays(1) -KeyExportPolicy Exportable
    $root = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
    $root.Open("ReadWrite")
    $root.Add($cert)
    $root.Close()
    $pfx = Join-Path $Dir "api.github.com.pfx"
    $pem = Join-Path $Dir "api.github.com.pem"
    $key = Join-Path $Dir "api.github.com.key"
    $pass = "e2e"
    $bytes = $cert.Export("Pfx", $pass)
    [IO.File]::WriteAllBytes($pfx, $bytes)
    # Convert to PEM for the Python ssl server (openssl is on recent windows-latest images).
    $openssl = Get-Command openssl -ErrorAction SilentlyContinue
    if ($openssl) {
        & openssl pkcs12 -in $pfx -out $pem -nodes -password "pass:$pass" | Out-Null
        # Split cert/key if needed: Python can load a combined PEM as both.
        Copy-Item $pem $key -Force
    } else {
        # Fall back to exporting CER + use a tiny Python extractor already run below.
        python -c @"
from pathlib import Path
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
data = Path(r'$pfx').read_bytes()
key, cert, _ = pkcs12.load_key_and_certificates(data, b'$pass')
Path(r'$key').write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
Path(r'$pem').write_bytes(cert.public_bytes(Encoding.PEM))
"@
    }
    $hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
    $marker = "hearth-e2e-api.github.com"
    $hostsWas = Get-Content $hostsPath -Raw
    $hostsWas | Set-Content -Path (Join-Path $Dir "hosts.bak") -Encoding ascii
    if ($hostsWas -notmatch [regex]::Escape($marker)) {
        Add-Content -Path $hostsPath -Value "`r`n127.0.0.1 api.github.com # $marker" -Encoding ascii
    }
    ipconfig /flushdns | Out-Null
    $pidFile = Join-Path $Dir "server.pid"
    $outLog = Join-Path $Dir "server.out"
    $errLog = Join-Path $Dir "server.err"
    $script = Join-Path $RepoRoot "pack\ci_e2e_releases.py"
    $p = Start-Process -FilePath "python" -WindowStyle Hidden -ArgumentList @(
        $script, "--setup", $Setup, "--sums", $Sums, "--tag", $Tag,
        "--host", "api.github.com", "--port", "443",
        "--tls-cert", $pem, "--tls-key", $key, "--pid-file", $pidFile
    ) -PassThru -RedirectStandardOutput $outLog -RedirectStandardError $errLog -NoNewWindow
    Start-Sleep -Seconds 1
    if ($p.HasExited) {
        throw "HTTPS stand-in exited early: $(Get-Content $errLog -ErrorAction SilentlyContinue) $(Get-Content $outLog -ErrorAction SilentlyContinue)"
    }
    return @{
        Process = $p
        CertThumb = $cert.Thumbprint
        HostsPath = $hostsPath
        HostsBak = (Join-Path $Dir "hosts.bak")
        OutLog = $outLog
        ErrLog = $errLog
        Marker = $marker
    }
}

function Stop-HttpsGitHubStandIn($State) {
    if ($State -and $State.Process -and -not $State.Process.HasExited) {
        try { $State.Process.Kill() } catch {}
    }
    if ($State.HostsBak -and (Test-Path $State.HostsBak) -and $State.HostsPath) {
        Copy-Item $State.HostsBak $State.HostsPath -Force
        ipconfig /flushdns | Out-Null
    }
    if ($State.CertThumb) {
        foreach ($storeName in @("Root", "My")) {
            $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, "LocalMachine")
            $store.Open("ReadWrite")
            $hits = $store.Certificates | Where-Object { $_.Thumbprint -eq $State.CertThumb }
            foreach ($c in $hits) { $store.Remove($c) }
            $store.Close()
        }
    }
}

function Save-Text([string]$Path, [string]$Text) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    $Text | Set-Content -Path $Path -Encoding utf8
}

$previousResults = @()
$newToNewerPassed = $false
$httpsState = $null
$httpServer = $null

try {
    Write-Log "New setup: $NewSetup version $NewVersion"
    if (-not (Test-Path $NewSetup) -or -not (Test-Path $NewSums)) {
        throw "New release assets are missing"
    }

    if (-not $SkipPrevious) {
        foreach ($tag in $PreviousTags) {
            $caseDir = Join-Path $EvidenceDir "previous-to-new\$tag"
            New-Item -ItemType Directory -Force -Path $caseDir | Out-Null
            $case = [ordered]@{ tag = $tag; passed = $false; reason = "" }
            $installDir = $null
            $newDir = $null
            try {
                Write-Log "==== previous $tag -> $NewVersion (real installed updater) ===="
                $prevSetup = Join-Path $caseDir "HearthSetup-previous.exe"
                gh release download $tag --repo AhmiDarrow/Hearth --pattern "HearthSetup.exe" --dir $caseDir --clobber
                $downloaded = Get-ChildItem $caseDir -Filter "HearthSetup.exe" | Select-Object -First 1
                if (-not $downloaded) { throw "Could not download $tag HearthSetup.exe" }
                Copy-Item $downloaded.FullName $prevSetup -Force
                $installDir = Join-Path $env:RUNNER_TEMP "hearth-e2e-$tag"
                $exe = Extract-Setup $prevSetup $installDir
                Save-Text (Join-Path $caseDir "installed-exe.txt") $exe
                $homeDir = Join-Path $env:RUNNER_TEMP "hearth-e2e-home-$tag"
                if ($httpsState) { Stop-HttpsGitHubStandIn $httpsState }
                $httpsState = Start-HttpsGitHubStandIn $NewSetup $NewSums "v$NewVersion" $caseDir
                $proc = Start-Hearth $exe $homeDir 17501 @{}
                $state = Wait-ApiState 17501 60 (Join-Path $caseDir "state-before.json")
                Save-Text (Join-Path $caseDir "running-version.txt") ("current=" + $state.version)
                Save-Screenshot (Join-Path $caseDir "screenshot-before.png")
                $detect = Invoke-UpdateCheck 17501 $false (Join-Path $caseDir "detect.json")
                Write-Log "detect status=$($detect.Status) raw=$($detect.Raw)"
                $install = Invoke-UpdateCheck 17501 $true (Join-Path $caseDir "install.json")
                Write-Log "install status=$($install.Status) raw=$($install.Raw)"
                Save-Screenshot (Join-Path $caseDir "screenshot-after-check.png")
                $setupPath = $null
                if ($install.Body -and $install.Body.setup) { $setupPath = [string]$install.Body.setup }
                if ($setupPath -and (Test-Path $setupPath)) {
                    Stop-Hearth $proc $installDir
                    $newDir = Join-Path $env:RUNNER_TEMP "hearth-e2e-updated-$tag"
                    $newExe = Install-Setup $setupPath $newDir
                    $home2 = Join-Path $env:RUNNER_TEMP "hearth-e2e-home-$tag-after"
                    $proc2 = Start-Hearth $newExe $home2 17502 @{}
                    $after = Wait-ApiState 17502 60 (Join-Path $caseDir "state-after.json")
                    Save-Screenshot (Join-Path $caseDir "screenshot-after.png")
                    Write-Log "relaunched version=$($after.version)"
                    Stop-Hearth $proc2 $newDir
                    Uninstall-Hearth $newDir
                    if ($after.version -eq $NewVersion) {
                        $case.passed = $true
                        $case.reason = "updated to $NewVersion"
                    } else {
                        $case.reason = "relaunched version $($after.version), expected $NewVersion"
                    }
                } else {
                    Stop-Hearth $proc $installDir
                    Uninstall-Hearth $installDir
                    $case.reason = "updater did not produce a verified installer. status=$($install.Status) body=$($install.Raw)"
                }
            } catch {
                $case.reason = "$_"
                Write-Log "previous $tag FAILED: $_"
                if ($installDir) { Stop-HearthUnder $installDir }
                if ($newDir) { Stop-HearthUnder $newDir }
            } finally {
                if ($httpsState) { Stop-HttpsGitHubStandIn $httpsState; $httpsState = $null }
            }
            $case | ConvertTo-Json | Set-Content (Join-Path $caseDir "result.json") -Encoding utf8
            $previousResults += [pscustomobject]$case
            Write-Log "previous $tag result passed=$($case.passed) reason=$($case.reason)"
        }
    }

    if ($NewerSetup -and $NewerSums -and $NewerVersion) {
        Write-Log "==== new $NewVersion -> newer $NewerVersion (fixed updater) ===="
        $caseDir = Join-Path $EvidenceDir "new-to-newer"
        New-Item -ItemType Directory -Force -Path $caseDir | Out-Null
        $installDir = Join-Path $env:RUNNER_TEMP "hearth-e2e-new"
        $exe = Install-Setup $NewSetup $installDir
        $serverDir = Join-Path $caseDir "server"
        New-Item -ItemType Directory -Force -Path $serverDir | Out-Null
        $httpServer = Start-HttpReleases $NewerSetup $NewerSums "v$NewerVersion" 18765 $serverDir
        Write-Log "fake releases at $($httpServer.Url)"
        $homeDir = Join-Path $env:RUNNER_TEMP "hearth-e2e-home-new"
        $extra = @{
            HEARTH_RELEASES_URL = $httpServer.Url
            HEARTH_UPDATE_SKIP_OPEN = "1"
        }
        $proc = Start-Hearth $exe $homeDir 17511 $extra
        $state = Wait-ApiState 17511 60 (Join-Path $caseDir "state-before.json")
        Save-Screenshot (Join-Path $caseDir "screenshot-before.png")
        Write-Log "running new build version=$($state.version)"
        if ($state.version -ne $NewVersion) {
            throw "Installed new build reported version $($state.version), expected $NewVersion"
        }
        $detect = Invoke-UpdateCheck 17511 $false (Join-Path $caseDir "detect.json")
        Write-Log "detect status=$($detect.Status) raw=$($detect.Raw)"
        if (-not $detect.Body -or -not $detect.Body.available) {
            throw ("Fixed updater did not detect {0}: {1}" -f $NewerVersion, $detect.Raw)
        }
        $install = Invoke-UpdateCheck 17511 $true (Join-Path $caseDir "install.json")
        Write-Log "install status=$($install.Status) raw=$($install.Raw)"
        Save-Screenshot (Join-Path $caseDir "screenshot-after-check.png")
        if ($install.Status -ne 200 -or -not $install.Body -or -not $install.Body.setup) {
            throw ("Fixed updater failed to download/verify {0}: {1}" -f $NewerVersion, $install.Raw)
        }
        $setupPath = [string]$install.Body.setup
        if (-not (Test-Path $setupPath)) { throw "Verified setup missing: $setupPath" }
        Stop-Hearth $proc $installDir
        $newerDir = Join-Path $env:RUNNER_TEMP "hearth-e2e-newer"
        $newerExe = Install-Setup $setupPath $newerDir
        $home2 = Join-Path $env:RUNNER_TEMP "hearth-e2e-home-newer"
        $proc2 = Start-Hearth $newerExe $home2 17512 @{}
        $after = Wait-ApiState 17512 60 (Join-Path $caseDir "state-after.json")
        Save-Screenshot (Join-Path $caseDir "screenshot-after.png")
        Write-Log "relaunched newer version=$($after.version)"
        Stop-Hearth $proc2 $newerDir
        Uninstall-Hearth $newerDir
        Uninstall-Hearth $installDir
        if ($after.version -ne $NewerVersion) {
            throw "Relaunched version $($after.version), expected $NewerVersion"
        }
        "PASSED $NewVersion -> $NewerVersion" | Set-Content (Join-Path $caseDir "PASSED") -Encoding utf8
        $newToNewerPassed = $true
    } else {
        Write-Log "No throwaway newer build provided; skipping new -> newer"
    }
} finally {
    if ($httpServer -and $httpServer.Process -and -not $httpServer.Process.HasExited) {
        try { $httpServer.Process.Kill() } catch {}
    }
    if ($httpsState) { Stop-HttpsGitHubStandIn $httpsState }
    Stop-Transcript | Out-Null
}

$summary = [ordered]@{
    newVersion = $NewVersion
    newerVersion = $NewerVersion
    newToNewerPassed = $newToNewerPassed
    previous = $previousResults
}
$summaryPath = Join-Path $EvidenceDir "summary.json"
$summary | ConvertTo-Json -Depth 6 | Set-Content $summaryPath -Encoding utf8
Write-Host "==== E2E summary ===="
Get-Content $summaryPath

if (-not $newToNewerPassed -and $NewerSetup) {
    throw "Required new -> newer auto-update test did not pass. Not safe to publish to Latest."
}
if ($RequirePrevious) {
    $ok = $previousResults | Where-Object { $_.passed }
    if (-not $ok) {
        throw "RequirePrevious is set, but no previous -> new update passed."
    }
}

# Previous -> new is expected to fail on v0.3.4/v0.3.6 because those
# frozen updaters truncate 64-bit CryptQueryObject pointers and report
# a correctly signed installer as not Authenticode-signed. Do not fail
# the job for that known field limitation.
$failedPrev = @($previousResults | Where-Object { -not $_.passed })
if ($failedPrev.Count -gt 0) {
    Write-Warning "previous -> new did not auto-update ($($failedPrev.Count) case(s)). Users on those builds need a one-time manual install of v$NewVersion. See $EvidenceDir."
}

Write-Host "E2E finished. Evidence: $EvidenceDir"
