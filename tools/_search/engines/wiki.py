"""游戏/ACG wiki 搜索引擎（best-effort，基于 Bing 有机结果按域名过滤）。

实测教训：Bing CN 把 ``site:`` 当**软提示**而非硬过滤，``site:a OR site:b``
反而会打乱排序（返回字典/官网等无关结果），单 ``site:`` 也不被严格尊重。
所以这里**不做站内限定查询**，改为：跑一次普通 Bing 查询，再按域名白名单
过滤出 wiki 命中。游戏类问题 Bing 会有机地把权威 wiki 页（BWIKI/萌娘/Fandom）
排进前列，过滤后即得「该问题对应的 wiki 词条」，并打上站点名 meta。

单页通常只有 0-2 条 wiki 命中；命中 0 条时返回空列表（不报错），
由 core 当「该引擎无结果」处理，web 引擎仍会覆盖该问题。
"""
from __future__ import annotations

from urllib.parse import urlparse

from ..models import SearchResult
from .bing import bing_query

# 域名白名单：netloc 命中其一即认定为 wiki 结果（覆盖中文 gacha + 欧美/主机 + 通用）
WIKI_DOMAINS = [
    "wiki.biligame.com",   # BWIKI：原神/星铁/绝区零/鸣潮/明日方舟…近官方
    "moegirl.org.cn",      # 萌娘百科：角色/剧情/设定（含 zh./mzh. 子域）
    "huijiwiki.com",       # 灰机wiki：MC/泰拉瑞亚等中文 wiki
    "wiki.gg",             # wiki.gg：灰机国际版（泰拉瑞亚等迁出 Fandom 的 wiki）
    "fandom.com",          # Fandom：欧美/主机游戏
    "fextralife.com",      # Fextralife：魂系/Elden Ring 等欧美攻略 wiki
]

_SITE_NAMES = {
    "wiki.biligame.com": "BWIKI",
    "moegirl.org": "萌娘百科",
    "huijiwiki.com": "灰机wiki",
    "wiki.gg": "wiki.gg",
    "fandom.com": "Fandom",
    "fextralife.com": "Fextralife",
}


def _site_name(url: str) -> str:
    """从 URL 推断 wiki 站点名，给结果加个一眼能认的 meta。"""
    netloc = urlparse(url).netloc.lower()
    for dom, name in _SITE_NAMES.items():
        if dom in netloc:
            return name
    return ""


def search(query: str, count: int = 5) -> list[SearchResult]:
    """普通 Bing 查询 + 域名白名单过滤，挑出 wiki 词条页。

    过取（count*3，下限 12）再过滤——Bing 单页约 10 条，wiki 命中通常 0-2。
    """
    raw = bing_query(query, max(count * 3, 12), engine="wiki")
    results: list[SearchResult] = []
    for r in raw:
        host = urlparse(r.url).netloc.lower()
        if not any(d in host for d in WIKI_DOMAINS):
            continue
        name = _site_name(r.url)
        if name:
            r.extra["meta"] = name
        r.rank = len(results) + 1
        results.append(r)
        if len(results) >= count:
            break
    return results
