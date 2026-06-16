"""统一搜索工具：一个 search 工具 + sources 参数，取代原来的多个搜索工具。

实现全在 ``tools/_search/`` 子包里（前缀 _ 不会被 @tool 扫描注册），
这里只暴露一个 ``search`` 给 LLM。
"""
from __future__ import annotations

from ai_agent import tool

from ._search import format_results, run_search


@tool
def search(query: str, sources: str = "auto", count: int = 5) -> str:
    """联网搜索（统一入口）。一个工具搞定网页 / 代码 / 视频 / 学术多种来源，自动并发、合并去重。

    什么时候用：
    - 需要**实时信息**：天气、新闻、价格、汇率、版本号、最近动态
    - 不确定某事实，或它可能在你的训练截止之后
    - 找代码库 / 报错解法 / 教程视频 / 论文

    什么时候**不**用：
    - 纯数学 / 逻辑推理；用户已上传文件的内容分析
    - 已知具体网址要读正文 → 用 fetch_webpage

    参数:
        query: 搜索关键词
        sources: 来源，默认 "auto"（按 query 自动路由）。也可手动指定，逗号可组合：
            - 组名: web(Bing+DDG) / code(GitHub+StackOverflow) / video(B站) /
                    chinese(Bing+B站) / academic(arXiv) / wiki(游戏/ACG wiki) /
                    game(wiki+B站+TapTap) / all
            - 引擎名: bing / ddg / github / stackoverflow / bilibili / taptap /
                    arxiv / wiki
            例: "auto" / "web" / "game" / "wiki" / "web,github" / "bilibili"
        count: 每个引擎返回条数（默认 5，上限 10）；多引擎合并去重后总数可能更多

    返回: 带序号的结果列表，每条标注来源引擎、URL、摘要。
    单个引擎失败不影响其他（末尾会注明哪个引擎没返回）。
    """
    data = run_search(query, sources=sources, count=count)
    return format_results(data)
