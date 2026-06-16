"""TapTap 游戏社区搜索引擎（best-effort）。

TapTap 搜索页是 SPA，``requests`` 拿到的多是壳，常常解析不出数据；
返回空列表（不报错）由 core 当成「该引擎无结果」处理，不影响其他引擎。
仅在 sources 含 game / taptap 时启用，不进默认路由。
"""
from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from ..headers import with_referer
from ..models import SearchResult
from ..timeouts import ENGINE_TIMEOUT as TIMEOUT


def search(query: str, count: int = 5) -> list[SearchResult]:
    """抓 taptap.cn 搜索页里的 app 卡片。解析不到就返回空列表。"""
    resp = requests.get(
        "https://www.taptap.cn/search",
        params={"keyword": query, "type": "app"},
        headers=with_referer("https://www.taptap.cn/"),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results: list[SearchResult] = []
    seen: set[str] = set()
    for card in soup.select("a[href*='/app/']"):
        href = card.get("href", "")
        if not href.startswith("/app/"):
            continue
        app_id = href.rsplit("/", 1)[-1]
        if app_id in seen:
            continue
        seen.add(app_id)
        title_el = card.select_one(".title, .app-name, h3, span")
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        score_el = card.select_one(".score, .rating, [class*=score]")
        score = score_el.get_text(strip=True) if score_el else ""
        results.append(SearchResult(
            title=title,
            url=f"https://www.taptap.cn{href}",
            snippet="",
            engine="taptap",
            rank=len(results) + 1,
            extra={"score": score} if score else {},
        ))
        if len(results) >= count:
            break
    return results
