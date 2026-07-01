@echo off
REM Yuki one-click installer for Windows.
REM Steps: check python -> create .venv -> install deps -> setup .env -> download model -> run check_install.

setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

cd /d "%~dp0"

echo.
echo ============================================================
echo   Yuki one-click installer
echo ============================================================
echo.

REM ---- 1. Check Python ----
echo [1/5] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found.
    echo.
    echo   Install Python 3.11 ^(recommended^) from:
    echo     https://www.python.org/downloads/release/python-3119/
    echo   During install: check "Add python.exe to PATH"
    echo   Then open a NEW command window and run install.bat again.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v

REM Version check: need 3.10+
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python !PYVER! is too old. Need 3.10 or newer ^(3.11 recommended^).
    echo   Install from: https://www.python.org/downloads/release/python-3119/
    echo   During install: check "Add python.exe to PATH"
    pause
    exit /b 1
)

REM MSYS2 / MinGW / Cygwin check:
REM These create .venv\bin\ (Unix-style) instead of .venv\Scripts\, breaking this installer.
for /f "delims=" %%p in ('python -c "import sys; print(sys.executable)"') do set PYEXE=%%p
echo !PYEXE! | findstr /i "msys64 mingw cygwin ucrt64 clang64" >nul 2>&1
if not errorlevel 1 (
    echo   [WARN] MSYS2/MinGW Python detected ^(!PYEXE!^).
    echo   Searching for official Windows Python via py launcher...
    set PYEXE=
    for %%n in (3.13 3.12 3.11 3.10) do (
        if not defined PYEXE (
            py -%%n --version >nul 2>&1
            if not errorlevel 1 (
                for /f "delims=" %%p in ('py -%%n -c "import sys; print(sys.executable)"') do set PYEXE=%%p
            )
        )
    )
    if not defined PYEXE (
        echo.
        echo   [ERROR] No official Windows Python found.
        echo   Install Python 3.11 from: https://www.python.org/downloads/release/python-3119/
        echo   During install: check "Add python.exe to PATH"
        echo   Then open a NEW command window and run install.bat again.
        pause
        exit /b 1
    )
    for /f "tokens=2" %%w in ('"!PYEXE!" --version 2^>^&1') do set PYVER=%%w
    echo   [AUTO] Using official Python !PYVER!: !PYEXE!
)

echo   OK: Python !PYVER! ^(!PYEXE!^)

REM ---- 2. Create .venv ----
echo.
echo [2/5] Creating virtual environment (.venv)...
if exist .venv (
    echo   .venv already exists, reusing
) else (
    "!PYEXE!" -m venv .venv
    if errorlevel 1 (
        echo   [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
)

REM Sanity check: make sure .venv\Scripts\python.exe really exists.
REM (If MSYS2 detection above somehow missed it, the venv would be Unix-style and fail here.)
if not exist ".venv\Scripts\python.exe" (
    echo   [ERROR] .venv\Scripts\python.exe not found after venv creation.
    echo   This usually means the Python that created it was MSYS2/MinGW.
    echo   Delete the .venv folder, install official Python 3.11 from python.org,
    echo   open a NEW command window, and run install.bat again.
    pause
    exit /b 1
)
echo   OK: .venv ready

REM ---- 3. Install dependencies ----
echo.
echo [3/5] Installing dependencies ^(this may take 2-10 minutes^)...
echo   Upgrading pip first...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet

echo   Installing packages from requirements.txt...
.venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [WARN] pip install failed on default PyPI. Retrying with Tsinghua mirror...
    echo   ^(This mirror is faster for users in mainland China^)
    .venv\Scripts\pip.exe install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo.
        echo   [ERROR] pip install failed on both PyPI and Tsinghua mirror.
        echo   Possible causes:
        echo     - No network connection
        echo     - Incompatible Python version ^(use Python 3.11 from python.org^)
        echo     - Disk space too low
        echo   Check the errors above, fix them, then run install.bat again.
        pause
        exit /b 1
    )
    echo   OK: dependencies installed ^(via Tsinghua mirror^)
) else (
    echo   OK: dependencies installed
)

REM ---- 4. Setup .env ----
echo.
echo [4/5] Setting up .env...
if not exist .env (
    if exist .env.example (
        copy /Y .env.example .env >nul
        echo   OK: .env created from .env.example
        echo.
        echo   ============================================================
        echo   ACTION REQUIRED: Fill in your DeepSeek API Key
        echo   ============================================================
        echo   Notepad will open with your .env file.
        echo   Find the line: DEEPSEEK_API_KEY=your_deepseek_key_here
        echo   Replace "your_deepseek_key_here" with your real key ^(sk-xxx...^)
        echo   Get a free key at: https://platform.deepseek.com/api_keys
        echo.
        echo   Save and CLOSE Notepad, then press any key here to continue.
        echo   ============================================================
        echo.
        start /wait notepad.exe .env
        echo   Continuing...
    ) else (
        echo   [WARN] .env.example missing, please create .env manually
    )
) else (
    echo   .env already exists ^(skipping^)
)

REM ---- 5. Download embedding model ----
echo.
echo [5/5] Checking embedding model ^(bge-base-zh-v1.5^)...
if exist "models\bge-base-zh-v1.5\pytorch_model.bin" (
    echo   OK: model already downloaded
) else (
    echo   Model not found. It is required for long-term memory ^(~390 MB^).
    echo   Will use https://hf-mirror.com ^(China-friendly mirror^).
    echo.
    set /p DOWNLOAD_MODEL=  Download now? [Y/n]:
    if /i "!DOWNLOAD_MODEL!"=="n" (
        echo.
        echo   [SKIPPED] Memory features will not work without the model.
        echo   To download later: run install.bat again, or follow models\README.md
    ) else (
        echo   Downloading... ^(this may take 5-15 minutes depending on your network^)
        set HF_ENDPOINT=https://hf-mirror.com
        REM Disable xet backend: hf-mirror does not proxy the xet CAS bridge,
        REM so large files would stall. Classic download works fine.
        set HF_HUB_DISABLE_XET=1
        .venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-base-zh-v1.5', local_dir='models/bge-base-zh-v1.5', local_dir_use_symlinks=False)"
        if errorlevel 1 (
            echo.
            echo   [ERROR] Download failed. Options:
            echo     1. Run install.bat again to retry
            echo     2. Download manually: see models\README.md for browser download links
            echo        ^(ModelScope mirror recommended for China^)
        ) else (
            echo   OK: model downloaded
        )
    )
)

REM ---- 6. Run completeness check ----
echo.
echo ============================================================
echo   Running completeness check...
echo ============================================================
.venv\Scripts\python.exe check_install.py
echo.

REM ---- 7. Finish ----
echo.
echo ============================================================
echo   Installation done.
echo ============================================================
echo.
echo   If the check above shows only .env / model warnings:
echo     - .env:   fill in DEEPSEEK_API_KEY ^(already opened in step 4^)
echo     - model:  download was either done or skipped above
echo   Both are expected -- they require your action, not a bug.
echo.
echo   Start ^(desktop mode, recommended^):
echo     .venv\Scripts\python launcher.py
echo.
echo   Or double-click launch.bat ^(no console window^)
echo.
echo   Or web mode ^(open http://127.0.0.1:3616^):
echo     .venv\Scripts\python server.py
echo.
pause
