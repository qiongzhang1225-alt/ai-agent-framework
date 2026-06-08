@echo off
REM Standalone WeChat iLink bridge for yuki.
REM Run this AFTER yuki server is up (launcher.py or server.py).
REM First time: scan the QR code with WeChat (settings -> plugins -> ClawBot).
REM Creds cached to .wechat_creds.json; later runs skip the QR.

chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo         Run install.bat first.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   yuki WeChat bridge (standalone)
echo ============================================================
echo.
echo   Default yuki API: http://127.0.0.1:3616
echo   To override:  set YUKI_API_BASE=http://your.host:port
echo.

.venv\Scripts\python.exe wechat_bridge.py

echo.
pause
