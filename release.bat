@echo off
REM Package a release zip (source only, no .venv / no model / no data).
REM Uses `git archive` to auto-respect .gitignore.
REM Output: yuki-source-<YYYYMMDD>.zip in the project root.

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

cd /d "%~dp0"

REM Date stamp YYYYMMDD
for /f "tokens=2 delims==" %%i in ('wmic os get LocalDateTime /value ^| find "="') do set DT=%%i
set DATE_STAMP=!DT:~0,8!
set OUTPUT=yuki-source-!DATE_STAMP!.zip

echo.
echo ============================================================
echo   Packaging release zip
echo ============================================================
echo.

REM Check git
where git >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] git not found
    pause
    exit /b 1
)

REM Check if git repo
if not exist .git (
    echo   [ERROR] Not a git repo. release.bat needs git tracking.
    echo   Run from a cloned repo, not extracted zip.
    pause
    exit /b 1
)

REM Remove old output
if exist "%OUTPUT%" (
    echo   Removing old %OUTPUT%
    del "%OUTPUT%"
)

REM git archive auto-excludes anything in .gitignore (.venv / models/bge / .sandbox / build / dist / etc)
echo   Creating archive from HEAD (auto-excludes .gitignore items)...
git archive --format=zip --output="%OUTPUT%" HEAD
if errorlevel 1 (
    echo   [ERROR] git archive failed
    pause
    exit /b 1
)

REM Stats
for %%F in ("%OUTPUT%") do set SIZE=%%~zF
set /a SIZE_KB=!SIZE!/1024
echo.
echo   OK: %OUTPUT% (!SIZE_KB! KB)
echo.

REM Verify essential files are in
echo   Verifying contents...
.venv\Scripts\python.exe -c "import zipfile; z = zipfile.ZipFile(r'%OUTPUT%'); names = z.namelist(); req = ['agent.py', 'server.py', 'launcher.py', 'install.bat', 'install.sh', 'check_install.py', 'requirements.txt', 'README.md', 'LICENSE', 'models/README.md', '.env.example', 'prompts/yuki.md', 'prompts/system.md']; missing = [r for r in req if r not in names]; print('  '+ ('OK: all essential files present' if not missing else 'MISSING: ' + ', '.join(missing)))"

echo.
echo   Distribute %OUTPUT% to users.
echo   They unzip + run install.bat (or install.sh) + launcher.py.
echo.
pause
