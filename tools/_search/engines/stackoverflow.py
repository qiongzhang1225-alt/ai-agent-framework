"""Stack Overflow 问答搜索引擎。"""
from __future__ import annotations

import requests

from ..models import SearchResult
from ..proxies import get_proxies
from ..timeouts import ENGINE_TIMEOUT as TIMEOUT


def search(query: str, count: int = 5) -> list[SearchResult]:
    """StackExchange 高级搜索 API，按票数排序。"""
    resp = requests.get(
        "https://api.stackexchange.com/2.3/search/advanced",
        params={
            "q": query,
            "site": "stackoverflow",
            "sort": "votes",
            "order": "desc",
            "pagesize": count,
        },
        timeout=TIMEOUT,
        proxies=get_proxies(),
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    results: list[SearchResult] = []
    for i, item in enumerate(items):
        tags = ", ".join(item.get("tags", [])[:3])
        results.append(SearchResult(
            title=item["title"],
            url=item["link"],
            snippet=f"标签: {tags}" if tags else "",
            engine="stackoverflow",
            rank=i + 1,
            extra={
                "score": item.get("score", 0),
                "answered": bool(item.get("is_answered")),
                "answers": item.get("answer_count", 0),
            },
        ))
    return results
