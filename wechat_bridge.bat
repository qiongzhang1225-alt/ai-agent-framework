@echo off
REM 独立启动微信 iLink Bot 桥接（不依赖 launcher.py 桌面端）
REM 用途: yuki 桌面端没开但想保持微信端可用 — 这个脚本只跑桥接进程
REM 前提: yuki 的 server 必须已在跑（launcher.py 或 server.py 起的都行）

chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo         先跑 install.bat 完成基础安装
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   yuki x 微信桥接（独立模式）
echo ============================================================
echo.
echo  默认连接: http://127.0.0.1:3616
echo  可设 set YUKI_API_BASE=http://其他地址 覆盖
echo.

.venv\Scripts\python.exe wechat_bridge.py

echo.
pause
