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

REM Clean old runtime files
if exist yuki.exe del /Q yuki.exe
if exist _internal rmdir /s /q _internal

REM Copy new ones
copy /Y "dist\yuki\yuki.exe" "yuki.exe" >nul
if errorlevel 1 (
    echo [ERROR] copy yuki.exe failed
    %_PAUSE%
    exit /b 1
)
xcopy /E /I /Q /Y "dist\yuki\_internal" "_internal" >nul
if errorlevel 1 (
    echo [ERROR] copy _internal\ failed
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
