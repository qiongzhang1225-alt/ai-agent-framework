"""项目根目录与常用子路径常量。

所有模块（agent.py / memory.py / tools/*）从这里 import 路径，
避免循环 import（之前 memory.py 反向 import agent.py 拿 _PROJECT_ROOT）。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
SANDBOX_DIR = PROJECT_ROOT / ".sandbox"
META_DIR = SANDBOX_DIR / "_meta"
DEFAULT_WORKDIR = SANDBOX_DIR / "workspace" / "default"
MEMORY_DIR = PROJECT_ROOT / ".memory"
SKILLS_DIR = PROJECT_ROOT / "skills"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
MODELS_DIR = PROJECT_ROOT / "models"
