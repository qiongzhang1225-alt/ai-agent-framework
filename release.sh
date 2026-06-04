#!/usr/bin/env bash
# Package a release zip (source only).
set -e

cd "$(dirname "$0")"

DATE_STAMP=$(date +%Y%m%d)
OUTPUT="yuki-source-${DATE_STAMP}.zip"

echo
echo "============================================================"
echo "  Packaging release zip"
echo "============================================================"
echo

# 找 git
command -v git >/dev/null 2>&1 || { echo "  [ERROR] git not found"; exit 1; }
[ -d .git ] || { echo "  [ERROR] Not a git repo"; exit 1; }

# 找 venv 的 python（用于检验）
if [ -f .venv/bin/python ]; then
    VENV_PY=.venv/bin/python
elif [ -f .venv/Scripts/python.exe ]; then
    VENV_PY=.venv/Scripts/python.exe
else
    VENV_PY=python
fi

# 清旧
[ -f "$OUTPUT" ] && rm -f "$OUTPUT"

echo "  Creating archive from HEAD (auto-excludes .gitignore items)..."
git archive --format=zip --output="$OUTPUT" HEAD

# 大小
SIZE_KB=$(($(stat -c%s "$OUTPUT" 2>/dev/null || stat -f%z "$OUTPUT") / 1024))
echo
echo "  OK: $OUTPUT ($SIZE_KB KB)"
echo

# 检验关键文件
echo "  Verifying contents..."
"$VENV_PY" -c "
import zipfile
z = zipfile.ZipFile('$OUTPUT')
names = z.namelist()
req = ['agent.py', 'server.py', 'launcher.py', 'install.bat', 'install.sh',
       'check_install.py', 'requirements.txt', 'README.md', 'LICENSE',
       'models/README.md', '.env.example', 'prompts/yuki.md', 'prompts/system.md']
missing = [r for r in req if r not in names]
print('  ' + ('OK: all essential files present' if not missing else 'MISSING: ' + ', '.join(missing)))
"

echo
echo "  Distribute $OUTPUT to users."
echo "  They unzip + run install.bat (Windows) or install.sh (Unix)."
echo
