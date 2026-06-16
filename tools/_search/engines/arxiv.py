"""arXiv 学术论文搜索引擎（academic 组）。

用官方 export API，返回 Atom XML，用 stdlib ElementTree 解析（不引新依赖）。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

from ..headers import BASE_HEADERS
from ..models import SearchResult
from ..proxies import get_proxies
from ..timeouts import ENGINE_TIMEOUT as TIMEOUT

_ATOM = "{http://www.w3.org/2005/Atom}"


def search(query: str, count: int = 5) -> list[SearchResult]:
    """arXiv export API，按相关度返回论文。"""
    resp = requests.get(
        "http://export.arxiv.org/api/query",
        params={
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": count,
        },
        headers=BASE_HEADERS,
        timeout=TIMEOUT,
        proxies=get_proxies(),
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    results: list[SearchResult] = []
    for i, entry in enumerate(root.findall(f"{_ATOM}entry")):
        title_el = entry.find(f"{_ATOM}title")
        summary_el = entry.find(f"{_ATOM}summary")
        id_el = entry.find(f"{_ATOM}id")
        if title_el is None or id_el is None:
            continue
        authors = [
            a.findtext(f"{_ATOM}name", "").strip()
            for a in entry.findall(f"{_ATOM}author")
        ]
        authors = [a for a in authors if a]
        author_str = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")
        results.append(SearchResult(
            title=" ".join((title_el.text or "").split()),
            url=(id_el.text or "").strip(),
            snippet=summary_el.text if summary_el is not None else "",
            engine="arxiv",
            rank=i + 1,
            extra={"meta": author_str} if author_str else {},
        ))
    return results
