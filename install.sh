#!/usr/bin/env bash
# Yuki one-click installer for macOS / Linux.

cd "$(dirname "$0")"

# 颜色
G='\033[32m'; R='\033[31m'; Y='\033[33m'; B='\033[1m'; D='\033[90m'; E='\033[0m'

echo
echo "============================================================"
echo "  Yuki one-click installer"
echo "============================================================"
echo

# ── 1. Python ──
echo -e "${B}[1/5] Checking Python...${E}"
PY=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
if [ -z "$PY" ]; then
    echo -e "  ${R}[ERROR]${E} Python not found."
    echo
    echo "  Install Python 3.11 (recommended):"
    echo "    macOS:  brew install python@3.11"
    echo "    Ubuntu: sudo apt install python3.11 python3.11-venv"
    echo "    Or download from: https://www.python.org/downloads/release/python-3119/"
    exit 1
fi

PYVER=$("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
PYMAJMIN=$("$PY" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')
if [ "$PYMAJMIN" -lt 310 ]; then
    echo -e "  ${R}[ERROR]${E} Python $PYVER < 3.10. Need 3.10+ (3.11 recommended)."
    echo "  Install from: https://www.python.org/downloads/release/python-3119/"
    exit 1
fi
echo -e "  ${G}OK${E}: Python $PYVER ($PY)"

# ── 2. .venv ──
echo
echo -e "${B}[2/5] Creating virtual environment (.venv)...${E}"
if [ -d .venv ]; then
    echo "  .venv already exists, reusing"
else
    "$PY" -m venv .venv
    if [ $? -ne 0 ]; then
        echo -e "  ${R}[ERROR]${E} Failed to create .venv."
        echo "  On Ubuntu/Debian you may need: sudo apt install python3-venv"
        exit 1
    fi
    echo -e "  ${G}OK${E}: .venv created"
fi

# 找 venv 的 python / pip
if [ -f .venv/bin/python ]; then
    VENV_PY=.venv/bin/python
    VENV_PIP=.venv/bin/pip
elif [ -f .venv/Scripts/python.exe ]; then
    VENV_PY=.venv/Scripts/python.exe
    VENV_PIP=.venv/Scripts/pip.exe
else
    echo -e "  ${R}[ERROR]${E} .venv created but python not found inside."
    exit 1
fi
echo -e "  ${G}OK${E}: venv python = $VENV_PY"

# ── 3. 依赖 ──
echo
echo -e "${B}[3/5] Installing dependencies (this may take 2-10 minutes)...${E}"
"$VENV_PY" -m pip install --upgrade pip --quiet

"$VENV_PIP" install -r requirements.txt
PIP_EXIT=$?
if [ $PIP_EXIT -ne 0 ]; then
    echo
    echo -e "  ${Y}[WARN]${E} pip install failed on default PyPI. Retrying with Tsinghua mirror..."
    echo "  (Faster for users in mainland China)"
    "$VENV_PIP" install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if [ $? -ne 0 ]; then
        echo
        echo -e "  ${R}[ERROR]${E} pip install failed on both PyPI and Tsinghua mirror."
        echo "  Possible causes:"
        echo "    - No network connection"
        echo "    - Incompatible Python version (use Python 3.11 from python.org)"
        echo "    - Disk space too low"
        echo "  Check the errors above, fix them, then run install.sh again."
        exit 1
    fi
    echo -e "  ${G}OK${E}: dependencies installed (via Tsinghua mirror)"
else
    echo -e "  ${G}OK${E}: dependencies installed"
fi

# ── 4. .env ──
echo
echo -e "${B}[4/5] Setting up .env...${E}"
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo -e "  ${G}OK${E}: .env created from .env.example"
        echo
        echo -e "  ${Y}============================================================${E}"
        echo -e "  ${Y}ACTION REQUIRED: Fill in your DeepSeek API Key${E}"
        echo -e "  ${Y}============================================================${E}"
        echo "  Open .env in your editor and find:"
        echo "    DEEPSEEK_API_KEY=your_deepseek_key_here"
        echo "  Replace 'your_deepseek_key_here' with your real key (sk-xxx...)"
        echo "  Get a free key at: https://platform.deepseek.com/api_keys"
        echo
        # Try to open with default editor
        if command -v open >/dev/null 2>&1; then
            open .env            # macOS
        elif command -v xdg-open >/dev/null 2>&1; then
            xdg-open .env 2>/dev/null &   # Linux (best effort)
        fi
        echo "  (If your editor didn't open, edit the file manually: nano .env)"
        echo
        read -r -p "  Press Enter when you have saved your API Key to continue..."
    else
        echo -e "  ${Y}[WARN]${E} .env.example missing, please create .env manually"
    fi
else
    echo "  .env already exists (skipping)"
fi

# ── 5. 模型 ──
echo
echo -e "${B}[5/5] Checking embedding model (bge-base-zh-v1.5)...${E}"
if [ -f "models/bge-base-zh-v1.5/pytorch_model.bin" ]; then
    echo -e "  ${G}OK${E}: model already downloaded"
else
    echo "  Model not found. It is required for long-term memory (~390 MB)."
    echo "  Will use https://hf-mirror.com (China-friendly mirror)."
    echo
    read -r -p "  Download now? [Y/n]: " ans
    if [[ "$ans" =~ ^[Nn]$ ]]; then
        echo -e "  ${Y}[SKIPPED]${E} Memory features will not work without the model."
        echo "  To download later: run install.sh again, or follow models/README.md"
    else
        echo "  Downloading... (may take 5-15 minutes depending on your network)"
        export HF_ENDPOINT=https://hf-mirror.com
        # Disable xet backend: hf-mirror does not proxy the xet CAS bridge,
        # so large files would stall. Classic download works fine.
        export HF_HUB_DISABLE_XET=1
        "$VENV_PY" -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-base-zh-v1.5', local_dir='models/bge-base-zh-v1.5', local_dir_use_symlinks=False)"
        if [ $? -ne 0 ]; then
            echo
            echo -e "  ${R}[ERROR]${E} Download failed. Options:"
            echo "    1. Run install.sh again to retry"
            echo "    2. Download manually: see models/README.md for browser download links"
        else
            echo -e "  ${G}OK${E}: model downloaded"
        fi
    fi
fi

# ── 6. 完整性检测 ──
echo
echo "============================================================"
echo "  Running completeness check..."
echo "============================================================"
"$VENV_PY" check_install.py || true
echo

# ── 7. 完成 ──
echo
echo "============================================================"
echo "  Installation done."
echo "============================================================"
echo
echo "  If the check above shows only .env / model warnings:"
echo "    - .env:  fill in DEEPSEEK_API_KEY (done in step 4)"
echo "    - model: downloaded or skipped above"
echo "  Both are expected -- they require your action, not a bug."
echo
echo "  Start (desktop mode, recommended):"
echo "    $VENV_PY launcher.py"
echo
echo "  Or double-click launch.sh"
echo
echo "  Or web mode (open http://127.0.0.1:3616):"
echo "    $VENV_PY server.py"
echo
