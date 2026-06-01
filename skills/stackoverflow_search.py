
def stackoverflow_search(query: str, sort: str = "votes", limit: int = 5) -> str:
    """搜索 Stack Overflow 技术问答。
    
    参数:
        query: 搜索关键词
        sort: 排序 - "votes" / "activity" / "relevance"
        limit: 返回数量 (1-10)
    """
    import requests
    import urllib.request
    
    url = "https://api.stackexchange.com/2.3/search/advanced"
    params = {
        "q": query,
        "site": "stackoverflow",
        "sort": sort,
        "pagesize": min(limit, 10),
        "order": "desc",
    }
    proxies = urllib.request.getproxies() or None
    
    try:
        resp = requests.get(url, params=params, timeout=15, proxies=proxies)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("items", []):
            title = item["title"]
            score = item.get("score", 0)
            answered = "✓" if item.get("is_answered") else "✗"
            tags = ", ".join(item.get("tags", [])[:3])
            results.append(f"{answered} [{score}] [{tags}] {title}\n{item['link']}")
        return "\n\n".join(results) if results else "无结果"
    except Exception as e:
        return f"Stack Overflow 搜索失败: {e}"
