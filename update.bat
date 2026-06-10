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
    REM ---- git clone path ----
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
    REM ---- zip download path ----
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
echo   [2/3] upgrading pip deps (in case requirements.txt changed)...
.venv\Scripts\pip.exe install -r requirements.txt --upgrade --quiet
if errorlevel 1 (
    echo   [WARN] pip had warnings - check output above.
)

echo.
echo   [3/3] checking yuki.exe...

REM Detect yuki.exe: user is on compiled version -> auto-rebuild to sync with new source.
REM Otherwise source-mode user already has new code via launcher.py.
if not exist "yuki.exe" (
    echo   No yuki.exe found - source mode user, skipping rebuild.
    goto done
)

REM yuki.exe running -> can't rebuild (_internal files would be locked)
tasklist /FI "IMAGENAME eq yuki.exe" 2>nul | find /I "yuki.exe" >nul
if not errorlevel 1 (
    echo   [WARN] yuki.exe is currently running.
    echo          Close it first ^(tray -^> exit^) then re-run update.bat.
    echo          Source code is already updated; just exe needs rebuild.
    pause
    exit /b 1
)

echo   yuki.exe found. Rebuild so the new source lands in the exe? ^(1-3 min^)
set /p REBUILD=  Continue? [Y/n]:
if /i "!REBUILD!"=="n" (
    echo   [SKIPPED] yuki.exe NOT rebuilt - it still has OLD code.
    echo            Either rerun update.bat with Y, or run build.bat later,
    echo            or run launcher.py from source instead.
    goto done
)

echo   Rebuilding yuki.exe...
set NOPAUSE=1
call build.bat
set NOPAUSE=
if errorlevel 1 (
    echo   [WARN] yuki.exe rebuild failed - check output above.
    echo          Source is updated; you can run launcher.py for now.
)

:done
echo.
echo ============================================================
echo   Update done
echo ============================================================
echo.
echo   Restart yuki to pick up changes ^(close from tray first, then launch^).
echo.
pause
