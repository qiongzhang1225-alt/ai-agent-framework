"""Bing CN 网页搜索引擎。"""
from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from ..headers import BASE_HEADERS
from ..models import SearchResult
from ..timeouts import ENGINE_TIMEOUT as TIMEOUT


def bing_query(q: str, count: int, engine: str = "bing") -> list[SearchResult]:
    """跑一次 cn.bing.com 查询并解析 li.b_algo。

    engine 标签可定制：wiki 引擎复用这套抓取逻辑（跑普通查询后按域名过滤出
    wiki 命中），只是把结果打上 engine="wiki"。失败抛异常由 core 记录。
    """
    resp = requests.get(
        "https://cn.bing.com/search",
        params={"q": q},
        headers=BASE_HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[SearchResult] = []
    for i, item in enumerate(soup.select("li.b_algo")):
        a_tag = item.select_one("h2 a")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        if not href:
            continue
        cap = item.select_one(".b_caption p") or item.select_one(".b_lineclamp2")
        results.append(SearchResult(
            title=a_tag.get_text(strip=True),
            url=href,
            snippet=cap.get_text(strip=True) if cap else "",
            engine=engine,
            rank=i + 1,
        ))
        if len(results) >= count:
            break
    return results


def search(query: str, count: int = 5) -> list[SearchResult]:
    """通用 Bing CN 网页搜索。"""
    return bing_query(query, count, "bing")
