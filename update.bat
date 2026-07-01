@echo off
REM Yuki updater launcher — minimal, almost never needs to change.
REM Does: fetch + reset --hard, then hands off to _update_core.bat (new version).

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo ============================================================
echo   Yuki one-click updater
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   [ERROR] .venv not found - run install.bat first.
    pause
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] git not found in PATH.
    echo   Install Git from: https://git-scm.com/download/win
    pause
    exit /b 1
)

if not exist ".git" (
    echo   No .git folder detected - zip install mode.
    echo.
    echo   Download latest zip and extract over this folder:
    echo     https://github.com/qiongzhang1225-alt/ai-agent-framework/archive/refs/heads/main.zip
    echo.
    echo   Then run update.bat again to upgrade dependencies.
    pause
    exit /b 0
)

REM Save current HEAD before any changes
for /f "delims=" %%h in ('git rev-parse HEAD 2^>nul') do set OLD_HEAD=%%h

echo [1/3] Fetching latest code...
git fetch origin
if errorlevel 1 (
    echo.
    echo   [ERROR] git fetch failed.
    echo   - GitHub unreachable: enable a VPN/proxy and try again
    echo   - No network connection
    pause
    exit /b 1
)

git reset --hard origin/main

REM Hand off to the new version of the core updater.
REM call opens _update_core.bat fresh from disk (already updated by reset --hard above).
call _update_core.bat "!OLD_HEAD!"
