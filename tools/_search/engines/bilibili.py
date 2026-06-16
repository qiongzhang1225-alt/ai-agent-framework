"""B站（bilibili.com）视频搜索引擎。

播放量 / UP主 / 时长放进 extra，由格式化层拼成无 emoji 的紧凑 meta。
"""
from __future__ import annotations

import re

import requests

from ..headers import with_referer
from ..models import SearchResult
from ..proxies import get_proxies
from ..timeouts import ENGINE_TIMEOUT as TIMEOUT


def _fmt_play(count: int) -> str:
    """播放量转成「万」。"""
    try:
        count = int(count)
    except (TypeError, ValueError):
        return ""
    if count >= 10000:
        return f"{count / 10000:.1f}万"
    return str(count)


def search(query: str, count: int = 5) -> list[SearchResult]:
    """B站官方搜索 API（search_type=video）。code != 0 抛异常由 core 记录。

    B站搜索接口有反爬：直接请求会 412 Precondition Failed，必须先访问主页
    拿到 buvid3 等指纹 cookie，再用同一 session 调搜索接口。
    """
    proxies = get_proxies()
    session = requests.Session()
    session.headers.update(with_referer("https://www.bilibili.com/"))
    # 预热：访问主页换取 buvid3/buvid4 cookie（绕过 412）
    try:
        session.get("https://www.bilibili.com/", timeout=TIMEOUT, proxies=proxies)
    except Exception:
        pass  # 预热失败也继续试，下面的请求会按需报错

    resp = session.get(
        "https://api.bilibili.com/x/web-interface/search/type",
        params={
            "search_type": "video",
            "keyword": query,
            "page": 1,
            "page_size": max(count, 10),
        },
        timeout=TIMEOUT,
        proxies=proxies,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站 API code={data.get('code')}: {data.get('message', '')}")

    results: list[SearchResult] = []
    for i, r in enumerate(data.get("data", {}).get("result", [])):
        bvid = r.get("bvid", "")
        if not bvid:
            continue
        title = re.sub(r"</?em[^>]*>", "", r.get("title", ""))  # 去高亮标签
        meta = []
        play = _fmt_play(r.get("play", 0))
        if play:
            meta.append(f"{play}播放")
        if r.get("author"):
            meta.append(f"UP {r['author']}")
        if r.get("duration"):
            meta.append(r["duration"])
        results.append(SearchResult(
            title=title,
            url=f"https://www.bilibili.com/video/{bvid}",
            snippet=re.sub(r"</?em[^>]*>", "", r.get("description", "")),
            engine="bilibili",
            rank=i + 1,
            extra={"meta": " · ".join(meta)},
        ))
        if len(results) >= count:
            break
    return results
