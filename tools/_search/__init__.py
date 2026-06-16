"""统一搜索子系统（**非 @tool 包**，前缀 _ 不会被工具注册扫描）。

对外只暴露 ``run_search`` 和 ``format_results``，给 ``tools/search.py`` 用。
内部分层：

- ``models``   —— SearchResult 数据类 + URL 归一化 + 截断
- ``proxies``  —— 系统代理读取
- ``headers``  —— 共享 User-Agent
- ``filters``  —— 词典/百科黑名单过滤
- ``cache``    —— 5 分钟 LRU 缓存
- ``router``   —— sources="auto" 时按关键词路由到引擎组
- ``engines/`` —— 各搜索引擎实现（bing/ddg/github/...），每个导出 search(query, count)
- ``core``     —— 编排：并发 + 超时 + 去重 + 缓存 + 降级 + 格式化

设计要点（区别于旧的 N 个独立工具）：
- LLM 只看到一个 ``search`` 工具，不用纠结"用哪个搜索"
- 统一返回格式，结果带 engine / rank 标注
- 单引擎失败不阻塞其他（graceful degradation）
"""
from __future__ import annotations

from .core import format_results, run_search

__all__ = ["run_search", "format_results"]
