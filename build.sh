#!/usr/bin/env bash
# 有希 macOS / Linux 一键打包脚本
# 产出:
#   Linux:   dist/yuki
#   macOS:   dist/yuki + dist/yuki.app
set -e

cd "$(dirname "$0")"

# 找 venv python
if [ -f ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -f ".venv/Scripts/python.exe" ]; then
    PY=".venv/Scripts/python.exe"
else
    echo "[ERROR] 找不到 .venv/bin/python"
    echo "        先建虚拟环境: python3 -m venv .venv"
    echo "        然后装依赖:    .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Linux 提示装系统 webview 依赖
if [[ "$(uname -s)" == "Linux" ]]; then
    if ! ldconfig -p 2>/dev/null | grep -q libwebkit2gtk; then
        echo "[WARN] 未检测到 libwebkit2gtk。Linux pywebview 需要："
        echo "       Ubuntu/Debian: sudo apt install python3-gi gir1.2-webkit2-4.0"
        echo "       Fedora:        sudo dnf install python3-webkitgtk4.0"
        # NOPAUSE=1 时（被 update.sh 调用）跳过交互式确认
        if [ "${NOPAUSE:-}" != "1" ]; then
            echo "       继续打包？[y/N]"
            read -r ans
            [[ "$ans" != "y" && "$ans" != "Y" ]] && exit 1
        fi
    fi
fi

echo
echo "=== 清理旧产物 ==="
rm -rf build dist

echo
echo "=== 开始打包 ==="
"$PY" -m PyInstaller yuki.spec --noconfirm

echo
echo "=== 完成 ==="
if [[ -f "dist/yuki" ]]; then
    echo "产出: $(pwd)/dist/yuki"
    ls -lh dist/yuki | awk '{print "体积: " $5}'
fi
if [[ -d "dist/yuki.app" ]]; then
    echo "Mac App: $(pwd)/dist/yuki.app"
    du -sh dist/yuki.app
fi
echo
echo "双击启动。首次启动会解压 prompts/ assets/ 到 exe 旁。"
