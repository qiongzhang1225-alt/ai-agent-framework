
def smart_search(query: str) -> str:
    """智能搜索：对实时类 query 提示补充 fetch_webpage 抓官方源，对技术类 query 优先官方文档。
    注意：此技能为搜索策略辅助，实际搜索仍需调用 web_search 工具。"""
    # 判断 query 类型
    realtime_keywords = ["天气", "今日", "今天", "现在", "最新", "股价", "汇率", "新闻", "实时"]
    tech_keywords = ["文档", "API", "benchmark", "教程", "官方", "Python", "代码", "配置"]
    
    is_realtime = any(kw in query for kw in realtime_keywords)
    is_tech = any(kw in query for kw in tech_keywords)
    
    advice = []
    if is_realtime:
        advice.append("实时查询 → 搜索后应补 fetch_webpage 抓官方源（如中央气象台、官方新闻站）")
    if is_tech:
        advice.append("技术查询 → 优先点击 python.org、github.com、官方文档域名")
    if not advice:
        advice.append("通用查询 → 搜索结果取前 3 条，必要时 fetch_webpage 深入")
    
    return "\n".join(advice)
