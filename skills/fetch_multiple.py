
def fetch_multiple(urls: list, max_chars: int = 2000) -> str:
    """批量抓取多个 URL 并返回各页面文本摘要。
    
    参数:
        urls: URL 字符串列表
        max_chars: 每页提取的最大字符数 (默认 2000)
    """
    import re
    import requests
    import urllib.request
    
    proxies = urllib.request.getproxies() or None
    results = []
    for i, url in enumerate(urls):
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Assistant/1.0)"
            }, proxies=proxies)
            resp.raise_for_status()
            text = resp.text
            
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            
            if len(text) > max_chars:
                text = text[:max_chars] + "..."
            
            results.append(f"【{i+1}】{url}\n{text}")
        except Exception as e:
            results.append(f"【{i+1}】{url}\n❌ 抓取失败: {e}")
    
    return "\n\n---\n\n".join(results)
