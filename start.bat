@echo off
chcp 65001 >nul
echo 正在启动 AI Agent Framework...
echo.
IF NOT EXIST ".venv\Scripts\python.exe" (
    echo 创建虚拟环境...
    python -m venv .venv
    echo 安装依赖...
    call .venv\Scripts\pip install -r requirements.txt
)
echo 设置 Hugging Face 镜像（避免下载超时）...
echo HF_ENDPOINT=https://hf-mirror.com >> .env
echo 启动 server...
.venv\Scripts\python server.py
pause
