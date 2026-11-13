@echo off
setlocal
set "CER=%~dp0Hearth.cer"
echo Importing the public Hearth publisher certificate into your Trusted Publishers store.
echo This does not install a root CA, and it will not silence Microsoft SmartScreen on first download.
certutil -user -addstore TrustedPublisher "%CER%"
if errorlevel 1 (
  echo If you want this for all users on this PC, run as Administrator:
  echo   certutil -addstore TrustedPublisher "%CER%"
  exit /b 1
)
echo Done. Future Hearth updates signed with this certificate should no longer show "Unknown publisher".
endlocal
