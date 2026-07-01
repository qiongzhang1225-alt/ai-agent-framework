@echo off
REM Yuki one-click updater for Windows.
REM Detects install mode (git clone vs zip download) and updates code + pip deps.
REM User data (.env / .sandbox / .memory / models / .venv / skills) preserved.

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo ============================================================
echo   Yuki one-click updater
echo ============================================================
echo.

REM ---- Pre-check: .venv must exist ----
if not exist ".venv\Scripts\python.exe" (
    echo   [ERROR] .venv not found - run install.bat first.
    pause
    exit /b 1
)

REM ---- Detect Python for pip (same MSYS2 fallback as install.bat) ----
set PYEXE=.venv\Scripts\python.exe

REM ================================================================
REM  Step 1: Update source code
REM ================================================================
if exist ".git" (
    REM ---- git clone path ----
    echo [1/3] Pulling latest code ^(git^)...
    where git >nul 2>&1
    if errorlevel 1 (
        echo   [ERROR] git not found in PATH.
        echo   Install Git from: https://git-scm.com/download/win
        pause
        exit /b 1
    )

    REM Save current HEAD so we can show what changed
    for /f "delims=" %%h in ('git rev-parse HEAD 2^>nul') do set OLD_HEAD=%%h

    git pull
    if errorlevel 1 (
        echo.
        echo   [ERROR] git pull failed. Common causes:
        echo     - You have local uncommitted changes.
        echo       Fix: git stash  then re-run update.bat
        echo     - No network connection.
        pause
        exit /b 1
    )

    REM Show what changed (if anything)
    for /f "delims=" %%h in ('git rev-parse HEAD 2^>nul') do set NEW_HEAD=%%h
    if "!OLD_HEAD!"=="!NEW_HEAD!" (
        echo   Already up to date - no new commits.
    ) else (
        echo.
        echo   Changes pulled:
        git log --oneline !OLD_HEAD!..HEAD
    )
    echo   OK
) else (
    REM ---- zip download path ----
    echo [1/3] Updating source code ^(zip mode^)...
    echo.
    echo   No .git folder found - you installed via zip download.
    echo.
    echo   To update, download the latest zip and extract it over this folder:
    echo     https://github.com/qiongzhang1225-alt/ai-agent-framework/archive/refs/heads/main.zip
    echo.
    echo   Steps:
    echo     1. Download the zip from the URL above
    echo     2. Open the zip - go into the "ai-agent-framework-main" subfolder
    echo     3. Select all files inside and copy them over this folder
    echo        ^(overwrite when prompted^)
    echo     4. Your data is safe: .env / .memory / models / .venv / skills
    echo        are not in the zip and will NOT be touched.
    echo     5. Come back here and press Y to continue with dependency update.
    echo.
    set /p CONTINUE=  Done copying? Continue? [Y/n]:
    if /i "!CONTINUE!"=="n" (
        echo   Cancelled.
        pause
        exit /b 0
    )
)

REM ================================================================
REM  Step 2: Upgrade pip dependencies
REM ================================================================
echo.
echo [2/3] Upgrading Python dependencies...
echo   ^(Only installs what changed in requirements.txt, usually fast^)
!PYEXE! -m pip install -r requirements.txt --upgrade
if errorlevel 1 (
    echo.
    echo   [WARN] pip upgrade had errors (see above).
    echo   Retrying with Tsinghua mirror...
    !PYEXE! -m pip install -r requirements.txt --upgrade -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo   [ERROR] pip upgrade failed on both mirrors. Check errors above.
    ) else (
        echo   OK: dependencies upgraded ^(via Tsinghua mirror^)
    )
) else (
    echo   OK: dependencies up to date
)

REM ================================================================
REM  Step 3: Verify installation
REM ================================================================
echo.
echo [3/3] Verifying installation...
!PYEXE! check_install.py
echo.

REM ================================================================
REM  Done
REM ================================================================
echo ============================================================
echo   Update done
echo ============================================================
echo.
echo   To apply changes: close Yuki from the tray, then relaunch.
echo.
echo   Start ^(desktop mode^):
echo     .venv\Scripts\python launcher.py
echo.
echo   Or web mode:
echo     .venv\Scripts\python server.py
echo.
timeout /t 2 /nobreak >nul
pause
