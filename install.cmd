@echo off
setlocal
cd /d "%~dp0"
echo.
echo  Hearth installer for Windows 11
echo  -------------------------------
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Install did not finish cleanly. See the messages above.
  pause
)
exit /b %ERR%
