#!/usr/bin/env bash
# yuki one-click updater for macOS / Linux.
# Detects install mode (git clone vs zip download) and updates code + pip deps.
# User data (.env / .sandbox / .memory / models / .venv / skills) preserved.

set -e
cd "$(dirname "$0")"

G='\033[32m'; R='\033[31m'; Y='\033[33m'; B='\033[1m'; D='\033[90m'; E='\033[0m'

echo
echo "============================================================"
echo "  yuki one-click update"
echo "============================================================"
echo

# 找 venv 的 pip
if [ -f .venv/bin/pip ]; then
    VENV_PIP=.venv/bin/pip
elif [ -f .venv/Scripts/pip.exe ]; then
    VENV_PIP=.venv/Scripts/pip.exe
else
    echo -e "  ${R}[ERROR]${E} .venv missing - run install.sh first."
    exit 1
fi

if [ -d .git ]; then
    # ── git clone path ──
    echo -e "  ${B}Mode:${E} git clone detected"
    echo
    if ! command -v git >/dev/null 2>&1; then
        echo -e "  ${R}[ERROR]${E} git not in PATH"
        exit 1
    fi
    echo -e "  ${B}[1/2]${E} git pull..."
    git pull
    echo -e "  ${G}OK${E}"
else
    # ── zip download path ──
    echo -e "  ${B}Mode:${E} zip install detected (no .git folder)"
    echo
    echo "  This script can't auto-download the new zip. Do this:"
    echo "    1. Browser: https://github.com/qiongzhang1225-alt/ai-agent-framework/releases/latest"
    echo "    2. Download yuki-source-*.zip"
    echo "    3. Unzip OVER this folder (overwrite when prompted)"
    echo "       Your data is safe: .env / .sandbox / .memory / models / .venv"
    echo "       are untouched (they aren't in the zip)."
    echo "    4. After unzipping, come back and answer Y below."
    echo
    read -p "  Done unzipping? Continue with pip upgrade [Y/n]: " ans
    if [ "${ans,,}" = "n" ]; then
        echo "  Cancelled."
        exit 0
    fi
fi

echo
echo -e "  ${B}[2/2]${E} upgrading pip deps (in case requirements.txt changed)..."
"$VENV_PIP" install -r requirements.txt --upgrade --quiet

echo
echo "============================================================"
echo -e "  ${G}Update done${E}"
echo "============================================================"
echo
echo "  Restart yuki to pick up changes:"
echo "    python launcher.py    # desktop mode"
echo "    python server.py      # web mode"
echo
