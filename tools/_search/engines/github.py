"""GitHub 仓库搜索引擎。"""
from __future__ import annotations

import requests

from ..models import SearchResult
from ..proxies import get_proxies
from ..timeouts import ENGINE_TIMEOUT as TIMEOUT


def search(query: str, count: int = 5) -> list[SearchResult]:
    """GitHub 仓库搜索 API。star 数放进 extra。"""
    resp = requests.get(
        "https://api.github.com/search/repositories",
        headers={"Accept": "application/vnd.github.v3+json"},
        params={"q": query, "per_page": count},
        timeout=TIMEOUT,
        proxies=get_proxies(),
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    results: list[SearchResult] = []
    for i, item in enumerate(items):
        stars = item.get("stargazers_count", 0)
        results.append(SearchResult(
            title=item["full_name"],
            url=item["html_url"],
            snippet=(item.get("description") or "").strip(),
            engine="github",
            rank=i + 1,
            extra={"stars": stars, "lang": item.get("language") or ""},
        ))
    return results
