"""搜索引擎注册表 + 来源分组。

显式 import 每个引擎（不用 pkgutil 动态发现），
这样 PyInstaller 静态分析能跟到，frozen 模式不漏模块。
"""
from __future__ import annotations

from collections.abc import Callable

from ..models import SearchResult
from . import arxiv, bilibili, bing, ddg, github, stackoverflow, taptap, wiki

# 引擎名 → search(query, count) -> list[SearchResult]
ENGINES: dict[str, Callable[[str, int], list[SearchResult]]] = {
    "bing": bing.search,
    "ddg": ddg.search,
    "github": github.search,
    "stackoverflow": stackoverflow.search,
    "bilibili": bilibili.search,
    "taptap": taptap.search,
    "arxiv": arxiv.search,
    "wiki": wiki.search,
}

# 来源组 → 引擎列表（sources 参数和路由器都用它）
GROUPS: dict[str, list[str]] = {
    "web": ["bing", "ddg"],
    "code": ["github", "stackoverflow"],
    "video": ["bilibili"],
    "chinese": ["bing", "bilibili"],
    "academic": ["arxiv"],
    "wiki": ["wiki"],
    "game": ["wiki", "bilibili", "taptap"],
    "all": ["bing", "ddg", "github", "stackoverflow", "bilibili", "arxiv", "wiki"],
}

__all__ = ["ENGINES", "GROUPS"]
