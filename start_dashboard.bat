@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo   Crypto Forecast Dashboard

echo   Local research dashboard - no live orders

echo ==============================================

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Streamlit/dependencies not found. Installing requirements...
    "%PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

echo [INFO] Opening dashboard in your browser...
"%PYTHON%" -m streamlit run dashboard.py

if errorlevel 1 pause
