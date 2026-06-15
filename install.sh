#!/usr/bin/env bash
# Yuki one-click installer for macOS / Linux.
set -e

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
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
    echo -e "  ${R}[ERROR]${E} Python not found. Install Python 3.10+ first."
    exit 1
fi
PYVER=$("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
PYMAJMIN=$("$PY" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')
if [ "$PYMAJMIN" -lt 310 ]; then
    echo -e "  ${R}[ERROR]${E} Python $PYVER < 3.10. Upgrade first."
    exit 1
fi
echo -e "  ${G}OK${E}: Python $PYVER ($PY)"

# ── 2. .venv ──
echo
echo -e "${B}[2/5] Creating virtual environment (.venv)...${E}"
if [ -d .venv ]; then
    echo "  .venv already exists, reuse"
else
    "$PY" -m venv .venv
    echo -e "  ${G}OK${E}: .venv created"
fi

# 找 venv 的 python (Windows / Unix 兼容)
if [ -f .venv/bin/python ]; then
    VENV_PY=.venv/bin/python
    VENV_PIP=.venv/bin/pip
elif [ -f .venv/Scripts/python.exe ]; then
    VENV_PY=.venv/Scripts/python.exe
    VENV_PIP=.venv/Scripts/pip.exe
else
    echo -e "  ${R}[ERROR]${E} .venv broken"
    exit 1
fi

# ── 3. 依赖 ──
echo
echo -e "${B}[3/5] Installing dependencies (2-5 min)...${E}"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PIP" install -r requirements.txt
echo -e "  ${G}OK${E}: dependencies installed"

# ── 4. .env ──
echo
echo -e "${B}[4/5] Setting up .env...${E}"
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo -e "  ${G}OK${E}: .env created from .env.example"
        echo -e "  ${Y}[TODO]${E} Edit .env to fill in your DEEPSEEK_API_KEY"
        echo "         https://platform.deepseek.com/api_keys"
    fi
else
    echo "  .env already exists"
fi

# ── 5. 模型 ──
echo
echo -e "${B}[5/5] Checking embedding model (bge-base-zh-v1.5)...${E}"
if [ -f "models/bge-base-zh-v1.5/pytorch_model.bin" ]; then
    echo -e "  ${G}OK${E}: model already downloaded"
else
    echo "  Model not found. Download now? ~390 MB."
    echo "  Will use https://hf-mirror.com mirror (China-friendly)."
    read -r -p "  Continue? [Y/n]: " ans
    if [[ "$ans" =~ ^[Nn]$ ]]; then
        echo -e "  ${Y}[SKIPPED]${E} Without the model, memory will not work."
        echo "  Run install.sh again later, or follow models/README.md"
    else
        echo "  Downloading..."
        export HF_ENDPOINT=https://hf-mirror.com
        # 关掉 xet 后端，否则大文件重定向到 hf-mirror 不代理的 cas-bridge.xethub.hf.co 会断流
        export HF_HUB_DISABLE_XET=1
        "$VENV_PY" -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-base-zh-v1.5', local_dir='models/bge-base-zh-v1.5')"
        echo -e "  ${G}OK${E}: model downloaded"
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
echo "  Start (desktop mode, recommended):"
echo "    $VENV_PY launcher.py"
echo
echo "  Or web mode (open http://127.0.0.1:3616):"
echo "    $VENV_PY server.py"
echo
