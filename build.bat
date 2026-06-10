@echo off
REM Yuki desktop build script for Windows.
REM See DESKTOP_BUILD.md for full doc (Chinese).
REM
REM Output: yuki.exe + _internal/ in project root.
REM Usage:  double-click, or run "build.bat" from cmd.
REM
REM Env var NOPAUSE=1 skips all pauses (used by update.bat for seamless rebuild).

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set _PAUSE=pause
if "%NOPAUSE%"=="1" set _PAUSE=rem

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo         Create venv first: python -m venv .venv
    echo         Then install deps: .venv\Scripts\pip install -r requirements.txt
    %_PAUSE%
    exit /b 1
)

REM yuki.exe must not be running (would lock _internal\ files)
tasklist /FI "IMAGENAME eq yuki.exe" 2>nul | find /I "yuki.exe" >nul
if not errorlevel 1 (
    echo [ERROR] yuki.exe is running. Close it first:
    echo         - tray icon ^-^> exit
    echo         - or: taskkill /F /IM yuki.exe
    %_PAUSE%
    exit /b 1
)

if not exist "assets\icon.ico" (
    echo [WARN] assets\icon.ico not found, exe will use default icon.
    echo        Generate with:
    echo        .venv\Scripts\python -c "from PIL import Image; Image.open('assets/icon.png').save('assets/icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
)

echo.
echo === Cleaning old artifacts ===
if exist build  rmdir /s /q build
if exist dist   rmdir /s /q dist

echo.
echo === Building (this may take 1-3 minutes) ===
.venv\Scripts\python.exe -m PyInstaller yuki.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] Build failed.
    %_PAUSE%
    exit /b 1
)

echo.
echo === Copying yuki/ to project root ===
REM onedir mode produces dist\yuki\yuki.exe + dist\yuki\_internal\
REM Copy yuki.exe to project root, _internal/ next to it.
REM yuki.exe needs _internal/ as a sibling to run.

REM Second check: yuki.exe may have been launched during build (1-3 min window)
tasklist /FI "IMAGENAME eq yuki.exe" 2>nul | find /I "yuki.exe" >nul
if not errorlevel 1 (
    echo [WARN] yuki.exe is running again ^(maybe tray launched it during build^).
    echo        Killing it so copy can proceed...
    taskkill /F /IM yuki.exe >nul 2>&1
    timeout /t 2 /nobreak >nul 2>&1
)

REM Clean old runtime files (del failures silent, copy error below catches them)
if exist yuki.exe del /F /Q yuki.exe >nul 2>&1
if exist _internal rmdir /s /q _internal >nul 2>&1

REM Copy new ones with retry (Windows file release can lag 0-3s after process exit)
set _COPY_OK=
for /L %%i in (1,1,3) do (
    if not defined _COPY_OK (
        copy /Y "dist\yuki\yuki.exe" "yuki.exe" >nul 2>&1
        if not errorlevel 1 set _COPY_OK=1
        if not defined _COPY_OK (
            echo   [retry %%i/3] yuki.exe locked, waiting 3s and retrying...
            timeout /t 3 /nobreak >nul 2>&1
            taskkill /F /IM yuki.exe >nul 2>&1
        )
    )
)
if not defined _COPY_OK (
    echo [ERROR] copy yuki.exe failed after 3 retries.
    echo         yuki.exe seems persistently locked.
    echo         Manual fix:
    echo           taskkill /F /IM yuki.exe
    echo           timeout /t 3
    echo           copy /Y "dist\yuki\yuki.exe" "yuki.exe"
    echo           xcopy /E /I /Q /Y "dist\yuki\_internal" "_internal"
    %_PAUSE%
    exit /b 1
)
xcopy /E /I /Q /Y "dist\yuki\_internal" "_internal" >nul
if errorlevel 1 (
    echo [ERROR] copy _internal\ failed - some file still locked.
    echo         Try the manual fix shown above.
    %_PAUSE%
    exit /b 1
)

echo Output: %CD%\yuki.exe + %CD%\_internal\
for %%F in (yuki.exe) do echo yuki.exe size: %%~zF bytes

echo.
echo === Done ===
echo.
echo Double-click yuki.exe to launch.
echo yuki.exe needs _internal\ as a sibling folder - don't separate them.
echo Original artifact at dist\yuki\.
echo.
%_PAUSE%
