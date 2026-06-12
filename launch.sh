#!/usr/bin/env bash
# yuki source-mode launcher (no rebuild needed after code changes).
# Difference vs yuki.exe (frozen): source mode reads launcher.py directly,
# so any code change applies after a simple restart.

cd "$(dirname "$0")"

# Find venv python (Linux/macOS or Windows-style venv)
if [ -f .venv/bin/python ]; then
    PY=.venv/bin/python
elif [ -f .venv/Scripts/python.exe ]; then
    PY=.venv/Scripts/python.exe
else
    echo "[ERROR] .venv missing - run install.sh first."
    exit 1
fi

# Detach so the terminal isn't tied to yuki
nohup "$PY" launcher.py >/dev/null 2>&1 &
echo "[OK] yuki launching (pid=$!)"
