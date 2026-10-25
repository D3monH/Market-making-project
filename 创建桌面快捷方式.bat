@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_desktop_shortcut.ps1"
if errorlevel 1 (
    echo.
    echo 创建桌面快捷方式失败，请把上面的错误截图发给我。
)
echo.
pause
