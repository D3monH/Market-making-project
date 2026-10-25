@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Crypto Quant Research Platform

echo ==============================================
echo   Crypto Quant Research Platform
echo   One-click local research dashboard
echo ==============================================

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] First run: creating local Python virtual environment...
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.11 -m venv .venv >nul 2>&1
        if errorlevel 1 py -m venv .venv
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 (
        echo [ERROR] Could not create .venv. Please install Python first.
        pause
        exit /b 1
    )
)

set "PYTHON=.venv\Scripts\python.exe"
set "PYTHONW=.venv\Scripts\pythonw.exe"

"%PYTHON%" -c "import streamlit, ccxt, sklearn, pandas" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing/updating project dependencies...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

rem Start the singleton background auto-forecast worker. If one is already
rem running, the new process exits immediately after failing to bind its port.
if exist "%PYTHONW%" (
    start "" /B "%PYTHONW%" auto_forecast_worker.py
) else (
    start "" /B "%PYTHON%" auto_forecast_worker.py
)

powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 (
    start "" "http://127.0.0.1:8501"
    exit /b 0
)

echo [INFO] Starting local dashboard...
"%PYTHON%" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless false

if errorlevel 1 pause
