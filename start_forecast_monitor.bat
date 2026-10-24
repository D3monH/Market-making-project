@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo ==============================================
echo   Forecast Verification Monitor
echo   Checks matured predictions every 60 seconds
echo ==============================================

"%PYTHON%" forecast_monitor.py --interval 60

if errorlevel 1 pause
