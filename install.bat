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

REM ── 1. Check Python ──
echo [1/5] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found. Install Python 3.10+ first:
    echo   https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   OK: Python !PYVER!

REM ── 2. Create .venv ──
echo.
echo [2/5] Creating virtual environment (.venv)...
if exist .venv (
    echo   .venv already exists, reuse
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo   [ERROR] Failed to create .venv
        pause
        exit /b 1
    )
    echo   OK: .venv created
)

REM ── 3. Install dependencies ──
echo.
echo [3/5] Installing dependencies (this may take 2-5 minutes)...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo   [ERROR] pip install failed. Check network or try:
    echo   .venv\Scripts\pip.exe install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)
echo   OK: dependencies installed

REM ── 4. Setup .env ──
echo.
echo [4/5] Setting up .env...
if not exist .env (
    if exist .env.example (
        copy /Y .env.example .env >nul
        echo   OK: .env created from .env.example
        echo   [TODO] Edit .env to fill in your DEEPSEEK_API_KEY
        echo          https://platform.deepseek.com/api_keys
    ) else (
        echo   [WARN] .env.example missing, please create .env manually
    )
) else (
    echo   .env already exists
)

REM ── 5. Download embedding model ──
echo.
echo [5/5] Checking embedding model (bge-base-zh-v1.5)...
if exist "models\bge-base-zh-v1.5\pytorch_model.bin" (
    echo   OK: model already downloaded
) else (
    echo   Model not found. Download now? It is about 390 MB.
    echo   Will use https://hf-mirror.com mirror for China-friendly speed.
    set /p DOWNLOAD_MODEL=  Continue? [Y/n]:
    if /i "!DOWNLOAD_MODEL!"=="n" (
        echo   [SKIPPED] Without the model, memory will not work.
        echo   You can run install.bat again later or download manually per models\README.md
    ) else (
        echo   Downloading...
        set HF_ENDPOINT=https://hf-mirror.com
        .venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-base-zh-v1.5', local_dir='models/bge-base-zh-v1.5', local_dir_use_symlinks=False)"
        if errorlevel 1 (
            echo   [ERROR] download failed
            echo   Try: edit models\README.md for manual download steps
        ) else (
            echo   OK: model downloaded
        )
    )
)

REM ── 6. Run completeness check ──
echo.
echo ============================================================
echo   Running completeness check...
echo ============================================================
.venv\Scripts\python.exe check_install.py
echo.

REM ── 7. Finish ──
echo.
echo ============================================================
echo   Installation done.
echo ============================================================
echo.
echo   Start (desktop mode, recommended):
echo     .venv\Scripts\python launcher.py
echo.
echo   Or web mode (open http://127.0.0.1:3616):
echo     .venv\Scripts\python server.py
echo.
echo   If completeness check above showed errors, fix them first.
echo.
pause
