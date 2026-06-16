"""DuckDuckGo 搜索引擎（中文/通用兜底）。

注意：包名已从 ``duckduckgo_search`` 改为 ``ddgs``（v9+），
旧的 ``from duckduckgo_search import DDGS`` 会 ImportError，
这正是之前 DDG 一直静默失效的根因。
"""
from __future__ import annotations

from ..models import SearchResult
from ..proxies import get_proxies
from ..timeouts import ENGINE_TIMEOUT as TIMEOUT


def search(query: str, count: int = 5) -> list[SearchResult]:
    """用 ddgs 库做文本搜索。库缺失/网络失败时抛异常由 core 记录。"""
    from ddgs import DDGS  # 包名是 ddgs，不是 duckduckgo_search

    results: list[SearchResult] = []
    proxies = get_proxies()
    proxy = (proxies or {}).get("https") or (proxies or {}).get("http")
    with DDGS(proxy=proxy, timeout=TIMEOUT) as ddgs:
        for i, r in enumerate(ddgs.text(query, max_results=count)):
            results.append(SearchResult(
                title=r.get("title", ""),
                url=r.get("href", "") or r.get("url", ""),
                snippet=r.get("body", ""),
                engine="ddg",
                rank=i + 1,
            ))
    return results
