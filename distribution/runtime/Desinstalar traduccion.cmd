@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0CavernOfDreams-ES\patch.ps1" -Mode Uninstall
set "patch_exit=%errorlevel%"
echo.
if /I not "%~1"=="--no-pause" pause
exit /b %patch_exit%
