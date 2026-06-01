
def github_search(query: str, type: str = "repositories", per_page: int = 5) -> str:
    """搜索 GitHub 仓库或代码。
    
    参数:
        query: 搜索关键词
        type: "repositories" 搜索仓库 / "code" 搜索代码
        per_page: 返回数量 (1-10)
    """
    import requests
    import urllib.request
    
    if type == "repositories":
        url = "https://api.github.com/search/repositories"
    else:
        url = "https://api.github.com/search/code"
    
    headers = {"Accept": "application/vnd.github.v3+json"}
    params = {"q": query, "per_page": min(per_page, 10)}
    proxies = urllib.request.getproxies() or None
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15, proxies=proxies)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("items", []):
            if type == "repositories":
                stars = item.get("stargazers_count", 0)
                desc = item.get("description") or "无描述"
                results.append(f"{item['full_name']} ⭐{stars} | {desc} | {item['html_url']}")
            else:
                repo = item["repository"]["full_name"]
                results.append(f"{repo} | {item['path']} | {item['html_url']}")
        return "\n\n".join(results) if results else "无结果"
    except Exception as e:
        return f"GitHub 搜索失败: {e}"
