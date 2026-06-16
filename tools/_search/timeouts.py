"""搜索超时常量（单独成模块，方便统一调参，且无任何 import 不会循环）。

- ENGINE_TIMEOUT: 单个引擎的 requests 超时。多数引擎会在此时间内失败返回。
- GLOBAL_TIMEOUT: 所有引擎并发后的总墙钟上限（真正的硬边界）。
  因为 ddg 库不老实遵守自身 timeout（实测能跑 20s），最终靠 core 里的
  wait(timeout=GLOBAL_TIMEOUT) + executor.shutdown(wait=False) 来兜底，
  保证用户最多等 GLOBAL_TIMEOUT 秒。
"""
from __future__ import annotations

ENGINE_TIMEOUT = 6       # 秒
GLOBAL_TIMEOUT = 7.0     # 秒（< 10，避免长时间死等）
