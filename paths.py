"""项目根目录与常用子路径常量。

所有模块（agent.py / memory.py / tools/*）从这里 import 路径，
避免循环 import（之前 memory.py 反向 import agent.py 拿 _PROJECT_ROOT）。

# 打包模式 vs 源码模式

PyInstaller `--onefile` 打包后：
- ``sys._MEIPASS`` 是临时解压目录（只读、重启重置）
- ``sys.executable`` 父目录是 .exe 旁的固定目录（可写、持久）

数据（.sandbox / .memory / skills / 用户改的 prompts）必须放固定目录，
代码资源（templates / static）可以在临时目录。

launcher.py 启动时已把可写资源（prompts/ assets/）从 bundle 解压到
``PROJECT_ROOT``（exe 旁），所以这里的 PROMPTS_DIR 直接用 PROJECT_ROOT。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 数据目录解析（PROJECT_ROOT）：
# 1. 环境变量 YUKI_DATA_DIR 最高优先（让用户能强制指定）
# 2. exe / paths.py 同级 + 向上 2 层 范围内有 .sandbox/.memory 的，用那个
#    （应对 dist/yuki.exe 跑在 E:\AI-Agent\dist\ 下时，能自动找到上一层
#    E:\AI-Agent\ 的真数据）
# 3. 都没找到 → 用 exe 同级（首次启动会在那里创建空目录）
import os as _os

def _resolve_data_root() -> Path:
    env_dir = _os.environ.get("YUKI_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).resolve()

    if getattr(sys, "frozen", False):
        start = Path(sys.executable).resolve().parent
    else:
        start = Path(__file__).resolve().parent

    # 自身 + 上 2 层找 .sandbox 或 .memory（数据存在的标志）
    for cand in (start, start.parent, start.parent.parent):
        if (cand / ".sandbox" / "_meta").exists() or (cand / ".memory").exists():
            return cand
    return start

PROJECT_ROOT = _resolve_data_root()

# 只读代码资源根（仅 frozen 模式下与 PROJECT_ROOT 不同）
# templates / static 等通过 server.py 的 StaticFiles 加载，
# 它们引用的路径需要走 BUNDLE_DIR 而非 PROJECT_ROOT
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", str(PROJECT_ROOT)))
else:
    BUNDLE_DIR = PROJECT_ROOT

SANDBOX_DIR = PROJECT_ROOT / ".sandbox"
META_DIR = SANDBOX_DIR / "_meta"
DEFAULT_WORKDIR = SANDBOX_DIR / "workspace" / "default"
MEMORY_DIR = PROJECT_ROOT / ".memory"
SKILLS_DIR = PROJECT_ROOT / "skills"

# prompts 默认在 PROJECT_ROOT（首次启动 launcher 已 seed）；
# 兜底：如果 PROJECT_ROOT 下不存在则用 BUNDLE_DIR 的只读版
_prompts_user = PROJECT_ROOT / "prompts"
PROMPTS_DIR = _prompts_user if _prompts_user.exists() else (BUNDLE_DIR / "prompts")

MODELS_DIR = PROJECT_ROOT / "models"

# server.py 用：templates / static 始终从 BUNDLE_DIR 读（只读）
TEMPLATES_DIR = BUNDLE_DIR / "templates"
STATIC_DIR = BUNDLE_DIR / "static"
ASSETS_DIR = (PROJECT_ROOT / "assets") if (PROJECT_ROOT / "assets").exists() else (BUNDLE_DIR / "assets")
