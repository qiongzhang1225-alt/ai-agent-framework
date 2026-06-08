@echo off
REM yuki one-click updater for Windows.
REM Detects install mode (git clone vs zip download) and updates code + pip deps.
REM User data (.env / .sandbox / .memory / models / .venv / skills) preserved.

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo ============================================================
echo   yuki one-click update
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   [ERROR] .venv missing - run install.bat first.
    pause
    exit /b 1
)

if exist ".git" (
    REM ── git clone path ──
    echo   Mode: git clone detected
    echo.
    where git >nul 2>&1
    if errorlevel 1 (
        echo   [ERROR] git not in PATH - install git or update zip-style.
        pause
        exit /b 1
    )
    echo   [1/2] git pull...
    git pull
    if errorlevel 1 (
        echo.
        echo   [ERROR] git pull failed - check conflicts above.
        echo   Manual fix: git stash + git pull + git stash pop
        pause
        exit /b 1
    )
    echo   OK
) else (
    REM ── zip download path ──
    echo   Mode: zip install detected (no .git folder)
    echo.
    echo   This script can't auto-download the new zip for you. Do this:
    echo     1. Browser: https://github.com/qiongzhang1225-alt/ai-agent-framework/releases/latest
    echo     2. Download yuki-source-*.zip
    echo     3. Unzip OVER this folder (overwrite when prompted)
    echo        Your data is safe: .env / .sandbox / .memory / models / .venv
    echo        are untouched (they aren't in the zip).
    echo     4. After unzipping, come back and answer Y below.
    echo.
    set /p CONTINUE=  Done unzipping? Continue with pip upgrade [Y/n]:
    if /i "!CONTINUE!"=="n" (
        echo   Cancelled.
        pause
        exit /b 0
    )
)

echo.
echo   [2/2] upgrading pip deps (in case requirements.txt changed)...
.venv\Scripts\pip.exe install -r requirements.txt --upgrade --quiet
if errorlevel 1 (
    echo   [WARN] pip had warnings - check output above.
)

echo.
echo ============================================================
echo   Update done
echo ============================================================
echo.
echo   Restart yuki to pick up changes:
echo     - desktop: close from tray, then run yuki.exe or launcher.py
echo     - if you use yuki.exe ^(not source^): you may also want to rebuild
echo       with build.bat so the new code lands in the exe.
echo.
pause
