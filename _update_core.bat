@echo off
REM Yuki update core — called by update.bat after git reset --hard.
REM Runs as a fresh process so it always reads the new version of this file.

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set OLD_HEAD=%~1
set PYEXE=.venv\Scripts\python.exe

REM ---- Show what changed ----
for /f "delims=" %%h in ('git rev-parse HEAD 2^>nul') do set NEW_HEAD=%%h
if "!OLD_HEAD!"=="!NEW_HEAD!" (
    echo   Already up to date - no new commits.
) else (
    echo.
    echo   Changes pulled:
    git log --oneline !OLD_HEAD!..HEAD
)
echo   OK

REM ---- Upgrade pip dependencies ----
echo.
echo [2/3] Upgrading Python dependencies...
echo   ^(Only installs what changed in requirements.txt, usually fast^)
!PYEXE! -m pip install -r requirements.txt --upgrade
if errorlevel 1 (
    echo.
    echo   [WARN] pip upgrade had errors ^(see above^).
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

REM ---- Verify installation ----
echo.
echo [3/3] Verifying installation...
!PYEXE! check_install.py
echo.

REM ---- Done ----
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
