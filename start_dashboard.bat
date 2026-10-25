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

echo [INFO] Starting local dashboard...
"%PYTHON%" -m streamlit run app.py --server.headless false

if errorlevel 1 pause
