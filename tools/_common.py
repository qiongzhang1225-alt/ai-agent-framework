"""工具内部共享的辅助函数（**非 @tool**）。

放在这里是为了让 ``execute.py`` 和 ``files.py`` 都能 import 同一份
``safe_workdir_path``，避免重复实现路径守卫。
"""
from __future__ import annotations

from pathlib import Path

from paths import DEFAULT_WORKDIR


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
