"""系统代理读取（搜索引擎共享）。"""
from __future__ import annotations

import urllib.request


def get_proxies() -> dict | None:
    """读取系统代理设置。返回 None 表示直连。"""
    p = urllib.request.getproxies()
    if p.get("http") or p.get("https"):
        return p
    return None
