# Start a process with no visible window. Use this for every CI child on the
# shared Windows desktop (consoles, Setup, Hearth, helpers).
function Start-HiddenProcess {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$ArgumentList = @(),
    [string]$WorkingDirectory = "",
    [switch]$Wait,
    [int]$TimeoutSec = 0,
    [string]$RedirectStandardOutput = "",
    [string]$RedirectStandardError = ""
  )
  $params = @{
    FilePath = $FilePath
    PassThru = $true
    WindowStyle = "Hidden"
  }
  if ($ArgumentList -and $ArgumentList.Count -gt 0) { $params.ArgumentList = $ArgumentList }
  if ($WorkingDirectory) { $params.WorkingDirectory = $WorkingDirectory }
  if ($RedirectStandardOutput) { $params.RedirectStandardOutput = $RedirectStandardOutput }
  if ($RedirectStandardError) { $params.RedirectStandardError = $RedirectStandardError }
  $proc = Start-Process @params
  if ($Wait -or $TimeoutSec -gt 0) {
    $ms = if ($TimeoutSec -gt 0) { $TimeoutSec * 1000 } else { [int]::MaxValue }
    if (-not $proc.WaitForExit($ms)) {
      try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
      throw "$FilePath did not exit within ${TimeoutSec}s"
    }
  }
  return $proc
}

# Inno Setup flags that must be on every CI install/uninstall. Never /SILENT
# (that shows a progress wizard) and never a visible window.
function Get-HearthSilentSetupArgs {
  param([string[]]$Extra = @())
  $args = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
  $args += $Extra
  return $args
}
