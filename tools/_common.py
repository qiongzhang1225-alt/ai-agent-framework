"""工具内部共享的辅助函数（**非 @tool**）。

放在这里是为了让 ``execute.py`` 和 ``files.py`` 都能 import 同一份
``safe_workdir_path``，避免重复实现路径守卫。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from paths import DEFAULT_WORKDIR


# ── frozen 模式下找真 Python 解释器（不是 yuki.exe）─────────────────────
# 打包后 sys.executable = yuki.exe（启动器），任何 subprocess.run([sys.executable, ...])
# 会启动 yuki.exe 子进程 → 跑 launcher.main → 撞 .yuki.lock → 弹"已启动"
# 死循环。必须找真正的 python.exe。
#
# 用于：tools/execute.py (跑用户代码), tools/self_edit.py (跑测试脚本)
_IS_FROZEN = getattr(sys, "frozen", False)
_cached_python_exe: str | None = None


def find_real_python() -> str | None:
    """找一个真的 Python 解释器（非 yuki.exe）。

    源码模式直接返回 sys.executable（venv 的 python.exe）。
    frozen 模式按优先级查找：
    1. 环境变量 YUKI_PYTHON
    2. exe 旁的 .venv/Scripts/python.exe
    3. PATH 中的 python / python3
    都没有返回 None（调用方报错提示用户）。

    结果会缓存（per-process）。
    """
    global _cached_python_exe

    if not _IS_FROZEN:
        return sys.executable

    if _cached_python_exe is not None:
        return _cached_python_exe or None

    env_py = os.environ.get("YUKI_PYTHON", "").strip()
    if env_py and Path(env_py).is_file():
        _cached_python_exe = env_py
        return env_py

    exe_dir = Path(sys.executable).resolve().parent
    for candidate in [
        exe_dir / "python.exe",
        exe_dir / ".venv" / "Scripts" / "python.exe",
        exe_dir / "venv" / "Scripts" / "python.exe",
        exe_dir.parent / ".venv" / "Scripts" / "python.exe",
    ]:
        if candidate.is_file():
            _cached_python_exe = str(candidate)
            return _cached_python_exe

    for name in ("python", "python3"):
        found = shutil.which(name)
        if found and "yuki.exe" not in found.lower():
            _cached_python_exe = found
            return found

    _cached_python_exe = ""
    return None


def safe_workdir_path(path: str, config: dict, *, must_exist: bool = False) -> Path:
    """把工具的 path 参数解析为绝对路径，并校验落在当前会话的 workdir 内。

    路径规则（跟 execute_code 的 _PREAMBLE 路径守卫一致）：
    - 相对路径 → 相对 workdir 解析
    - 绝对路径 → 必须仍在 workdir 内部（防 path traversal）
    - 解析后的路径必须可以 ``.relative_to(workdir)`` 成功

    Args:
        path: LLM 传入的路径字符串
        config: 工具的 config 参数（含 {"configurable": {"workdir": "..."}}）
        must_exist: True 时校验文件 / 目录必须已存在

    Raises:
        ValueError: 路径越界 / 必须存在但不存在
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    workdir = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    workdir.mkdir(parents=True, exist_ok=True)

    p = Path(path)
    if not p.is_absolute():
        p = workdir / p
    p = p.resolve()

    try:
        p.relative_to(workdir)
    except ValueError:
        raise ValueError(
            f"路径越界: {p} 不在工作目录 {workdir} 内。请使用相对路径或工作目录内的绝对路径。"
        )

    if must_exist and not p.exists():
        raise ValueError(f"路径不存在: {p}")

    return p
