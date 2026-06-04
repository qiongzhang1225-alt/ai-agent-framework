"""轻量启动时间打印（用于诊断启动慢的原因）。

环境变量 ``YUKI_LOG_TIMING=1`` 时启用，否则 ``mark()`` 是 no-op，
零运行时开销（一个 dict 查表 + 一个 if）。

用法:
    from timing import mark
    mark("阶段 A 开始")
    do_heavy_work()
    mark("阶段 A 完成")

输出格式（stderr）:
    [TIMING]   3.21s  (+0.45s)  阶段 A 完成

- 第 1 列: 从 T0（本模块首次 import）到现在的总时间
- 第 2 列: 距上一个 mark 的 delta
- 第 3 列: 自定义 label
"""
from __future__ import annotations

import os
import sys
import time

_T0 = time.monotonic()
_ENABLED = os.environ.get("YUKI_LOG_TIMING", "").strip().lower() in ("1", "true", "yes", "on")
_last = _T0


def mark(label: str) -> None:
    """打印阶段时间。YUKI_LOG_TIMING 关闭时即时返回（不打印）。"""
    global _last
    if not _ENABLED:
        return
    now = time.monotonic()
    elapsed_total = now - _T0
    delta = now - _last
    _last = now
    print(
        f"[TIMING] {elapsed_total:6.2f}s  (+{delta:5.2f}s)  {label}",
        file=sys.stderr,
        flush=True,
    )


def enabled() -> bool:
    """供其他模块查"timing 是否启用"，避免做不必要的子计算。"""
    return _ENABLED
