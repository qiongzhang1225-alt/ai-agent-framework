"""多引擎聚合搜索：并发查询 Bing + GitHub + Stack Overflow。"""
from __future__ import annotations

import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from ai_agent import tool

# ── 网络代理 ─────────────────────────────────────────────────────────────────


def _get_proxies() -> dict | None:
    """读取系统代理设置。返回 None 表示直连。"""
    p = urllib.request.getproxies()
    if p.get("http") or p.get("https"):
        return p
    return None

# ── 各引擎搜索函数 ───────────────────────────────────────────────────────────

_BING_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _search_bing(query: str) -> str:
    """Bing CN 搜索，返回格式化字符串。"""
    try:
        resp = requests.get(
            "https://cn.bing.com/search",
            params={"q": query},
            headers=_BING_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("li.b_algo")
        if not items:
            return ""
        lines = ["【Bing】"]
        for item in items[:5]:
            a_tag = item.select_one("h2 a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            cap = item.select_one(".b_caption p") or item.select_one(".b_lineclamp2")
            snippet = cap.get_text(strip=True) if cap else ""
            lines.append(f"- {title}\n  {href}\n  {snippet}")
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception as e:
        return f"【Bing】❌ {e}"


def _search_github(query: str) -> str:
    """GitHub 仓库搜索。"""
    try:
        url = "https://api.github.com/search/repositories"
        headers = {"Accept": "application/vnd.github.v3+json"}
        resp = requests.get(
            url, headers=headers,
            params={"q": query, "per_page": 5},
            timeout=10,
            proxies=_get_proxies(),
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return ""
        lines = ["【GitHub 仓库】"]
        for item in items:
            stars = item.get("stargazers_count", 0)
            desc = (item.get("description") or "").strip()
            lines.append(
                f"- {item['full_name']} ⭐{stars}\n"
                f"  {desc}\n"
                f"  {item['html_url']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"【GitHub】❌ {e}"


def _search_stackoverflow(query: str) -> str:
    """Stack Overflow 搜索。"""
    try:
        url = "https://api.stackexchange.com/2.3/search/advanced"
        params = {
            "q": query,
            "site": "stackoverflow",
            "sort": "votes",
            "pagesize": 5,
            "order": "desc",
        }
        resp = requests.get(url, params=params, timeout=10, proxies=_get_proxies())
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return ""
        lines = ["【Stack Overflow】"]
        for item in items:
            title = item["title"]
            score = item.get("score", 0)
            answered = "✓" if item.get("is_answered") else "✗"
            tags = ", ".join(item.get("tags", [])[:3])
            lines.append(
                f"- {answered} [{score}] [{tags}] {title}\n"
                f"  {item['link']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"【Stack Overflow】❌ {e}"


# ── 聚合搜索工具 ─────────────────────────────────────────────────────────────

ENGINE_MAP = {
    "bing": _search_bing,
    "github": _search_github,
    "stackoverflow": _search_stackoverflow,
}


@tool
def aggregate_search(query: str, sources: str = "all") -> str:
    """多引擎聚合搜索：同时查询 Bing + GitHub + Stack Overflow，并发返回合并结果。

    什么时候用：
    - 拿不准哪个源结果最好，想一次看多个源的
    - 技术类问题，既想看通用搜索又想看 GitHub/Stack Overflow
    - 需要交叉验证信息

    什么时候**不**用：
    - 简单事实查询（用 web_search 更快）
    - 只需要某个特定源（直接用 github_search / stackoverflow_search）

    参数:
        query: 搜索关键词
        sources: "all"(全部) / "bing" / "github" / "stackoverflow" / "bing,github" 逗号组合
    """
    # 解析 sources
    if sources == "all":
        selected = list(ENGINE_MAP.keys())
    else:
        selected = [
            s.strip()
            for s in sources.split(",")
            if s.strip() in ENGINE_MAP
        ]
    if not selected:
        return (
            f"无效的搜索源 '{sources}'。可选: "
            f"all / {', '.join(ENGINE_MAP.keys())}"
        )

    # 并发查询（每个引擎独立 15s 超时）
    results: list[str] = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(ENGINE_MAP[s], query): s for s in selected}
        for fut, name in futures.items():
            try:
                result = fut.result(timeout=15)
                if result and "❌" not in result.split("\n")[0]:
                    results.append(result)
            except Exception:
                pass  # 单个引擎失败不阻塞其他

    if not results:
        return f"聚合搜索 '{query}' 无结果（共 {len(selected)} 个引擎）"

    header = f"🔍 聚合搜索: {query}（{len(results)}/{len(selected)} 引擎有结果）\n"
    return header + "\n\n".join(results)
