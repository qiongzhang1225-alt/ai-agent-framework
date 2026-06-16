"""搜索编排：来源解析 → 并发执行 → 去重 → 缓存 → 降级 → 格式化。

设计取舍：
- 并发：ThreadPoolExecutor，所有选中引擎同时跑
- 超时：全局 wait(timeout=GLOBAL_TIMEOUT)，因并发，墙钟≈单引擎超时
       未完成的标记为 timeout 进 engines_failed，不拖累已完成的
- 去重：normalize_url 归一化后去重，保留更高优先级引擎的那条
- 缓存：5 分钟，key=(query, 引擎元组, count)
- 降级：任何单引擎抛错只记进 engines_failed，绝不向上抛
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import cache
from .engines import ENGINES, GROUPS
from .filters import is_dict_result
from .models import SearchResult, normalize_url
from .router import route
from .timeouts import GLOBAL_TIMEOUT

TOTAL_CAP = 20         # 合并去重后最多返回多少条，避免灌爆上下文
_WEB_ENGINES = {"bing", "ddg"}


def resolve_sources(query: str, sources: str) -> list[str]:
    """把 sources 参数解析成有序、去重的引擎名列表。

    - "auto"/"" → 走路由器按 query 猜组
    - 否则按逗号拆分，每个 token 可以是「组名」或「引擎名」
    - 未知 token 忽略；全空则回落到 web
    """
    sources = (sources or "").strip().lower()
    if sources in ("", "auto"):
        tokens = route(query)
    else:
        tokens = [t.strip() for t in sources.split(",") if t.strip()]

    engines: list[str] = []
    for tok in tokens:
        names = GROUPS.get(tok, [tok] if tok in ENGINES else [])
        for n in names:
            if n in ENGINES and n not in engines:
                engines.append(n)
    return engines or list(GROUPS["web"])


def run_search(query: str, sources: str = "auto", count: int = 5) -> dict:
    """执行一次搜索，返回结构化结果 dict（不抛异常）。

    返回:
        {
          query, sources, engines_used, engines_failed,
          results: list[SearchResult], elapsed_ms, cached
        }
    """
    query = (query or "").strip()
    count = max(1, min(int(count or 5), 10))
    if not query:
        return {
            "query": query, "sources": sources, "engines_used": [],
            "engines_failed": {}, "results": [], "elapsed_ms": 0,
            "cached": False, "error": "空查询",
        }

    engines = resolve_sources(query, sources)
    cache_key = (query, tuple(engines), count)
    cached = cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    t0 = time.perf_counter()
    collected: list[SearchResult] = []
    failed: dict[str, str] = {}
    used: list[str] = []

    # 注意：不用 `with ThreadPoolExecutor`——它在退出时 shutdown(wait=True)，
    # 会阻塞等所有线程（含不老实的 ddg，能跑 20s）跑完，让 GLOBAL_TIMEOUT 形同虚设。
    # 改为手动 shutdown(wait=False)：墙钟一到就返回，慢引擎线程后台自己结束。
    ex = ThreadPoolExecutor(max_workers=len(engines))
    try:
        future_to_name = {
            ex.submit(ENGINES[name], query, count): name for name in engines
        }
        done, not_done = wait(future_to_name.keys(), timeout=GLOBAL_TIMEOUT)
        for fut in done:
            name = future_to_name[fut]
            try:
                res = fut.result()
                if res:
                    collected.extend(res)
                used.append(name)
            except Exception as e:  # 单引擎失败只记录，不上抛
                failed[name] = f"{type(e).__name__}: {e}"[:120]
        for fut in not_done:
            failed[future_to_name[fut]] = "超时"
            fut.cancel()
    finally:
        ex.shutdown(wait=False)

    # 词典/百科/黄历过滤：只作用于通用网页引擎（bing/ddg）。
    # 回落判断看「全局」——只要别的引擎（wiki/B站/TapTap…）有结果，被判为污染的
    # 网页结果就直接丢，不让"卡_百度百科""今日老黄历"占前排。旧逻辑（filter_dict）
    # 在 web 全是污染时整段回落保留，导致污染排到第 1 条（正是 yuki 反馈的痛点）。
    web_raw = [r for r in collected if r.engine in _WEB_ENGINES]
    others = [r for r in collected if r.engine not in _WEB_ENGINES]
    web_kept = [r for r in web_raw if not is_dict_result(r)]
    if not web_kept and not others:   # 真的全空才回落，至少给点东西
        web_kept = web_raw
    merged = web_kept + others

    # 按引擎优先级（engines 顺序）+ 引擎内排名排序，再按归一化 URL 去重
    priority = {name: i for i, name in enumerate(engines)}
    merged.sort(key=lambda r: (priority.get(r.engine, 99), r.rank))
    seen: set[str] = set()
    deduped: list[SearchResult] = []
    for r in merged:
        key = normalize_url(r.url)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(r)
        if len(deduped) >= TOTAL_CAP:
            break

    result = {
        "query": query,
        "sources": sources,
        "engines_used": [n for n in engines if n in used],
        "engines_failed": failed,
        "results": deduped,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "cached": False,
    }
    # 只缓存有结果的（失败的下次重试）
    if deduped:
        cache.put(cache_key, result)
    return result


# ── 格式化：结构化 dict → 给 LLM 看的纯文本（无 emoji）──────────────────

def _meta_line(r: SearchResult) -> str:
    """把 extra 里的引擎特有元数据拼成一行紧凑 meta。"""
    e = r.extra or {}
    if r.engine == "github":
        bits = []
        if e.get("stars") is not None:
            bits.append(f"{e['stars']} star")
        if e.get("lang"):
            bits.append(e["lang"])
        return " · ".join(bits)
    if r.engine == "stackoverflow":
        bits = [f"得分 {e.get('score', 0)}"]
        bits.append("已解决" if e.get("answered") else "未解决")
        if e.get("answers"):
            bits.append(f"{e['answers']} 回答")
        return " · ".join(bits)
    if r.engine == "taptap" and e.get("score"):
        return f"评分 {e['score']}"
    return e.get("meta", "")  # bilibili / arxiv 直接给好了


def format_results(data: dict) -> str:
    """把 run_search 的结果 dict 渲染成纯文本。"""
    query = data.get("query", "")
    if data.get("error"):
        return f"搜索失败: {data['error']}"

    results = data.get("results", [])
    used = data.get("engines_used", [])
    failed = data.get("engines_failed", {})

    if not results:
        msg = f"搜索「{query}」无结果"
        if failed:
            fail_str = "；".join(f"{k} {v}" for k, v in failed.items())
            msg += f"（引擎均未返回: {fail_str}）"
        return msg

    head_bits = [f"引擎 {', '.join(used)}"] if used else []
    head_bits.append(f"{len(results)} 条")
    head_bits.append(f"{data.get('elapsed_ms', 0)}ms")
    if data.get("cached"):
        head_bits.append("缓存")
    lines = [f"搜索「{query}」· " + " · ".join(head_bits), ""]

    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r.engine}] {r.title}")
        lines.append(f"   {r.url}")
        if r.snippet:
            lines.append(f"   {r.snippet}")
        meta = _meta_line(r)
        if meta:
            lines.append(f"   {meta}")
        lines.append("")

    if failed:
        fail_str = "；".join(f"{k}({v})" for k, v in failed.items())
        lines.append(f"（部分引擎未返回: {fail_str}）")

    return "\n".join(lines).rstrip()
