@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 goto failed
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Startup failed. See the message above.
echo If port 8000 is busy, stop the old server or use scripts\start.ps1 -Port 8001.
pause
exit /b 1
