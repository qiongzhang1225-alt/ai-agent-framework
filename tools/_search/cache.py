"""5 分钟 TTL 的小缓存：同一 (query, engines, count) 短期内复用。

线程安全（搜索本身用线程池，但 get/set 只在主调用线程发生，
仍加锁防御并发的多个 search() 调用）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

TTL_SECONDS = 300  # 5 分钟
MAX_ENTRIES = 64

_store: "OrderedDict[Any, tuple[float, Any]]" = OrderedDict()
_lock = threading.Lock()


def get(key: Any) -> Any | None:
    """命中且未过期返回值，否则 None。"""
    now = time.time()
    with _lock:
        item = _store.get(key)
        if item is None:
            return None
        ts, value = item
        if now - ts > TTL_SECONDS:
            _store.pop(key, None)
            return None
        _store.move_to_end(key)  # LRU：最近用的放末尾
        return value


def put(key: Any, value: Any) -> None:
    """写入缓存并按 LRU 淘汰超量条目。"""
    with _lock:
        _store[key] = (time.time(), value)
        _store.move_to_end(key)
        while len(_store) > MAX_ENTRIES:
            _store.popitem(last=False)  # 淘汰最久未用


def clear() -> None:
    """清空缓存（测试用）。"""
    with _lock:
        _store.clear()
