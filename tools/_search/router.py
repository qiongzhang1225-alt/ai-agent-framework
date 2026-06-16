"""sources="auto" 时的关键词路由：按 query 猜测该查哪些来源组。

规则按顺序匹配、可累加（命中多条就合并去重）。都不命中 → web。
返回的是「来源组」名（web/code/video/...），由 core.resolve_sources 再展开成引擎。
"""
from __future__ import annotations

import re

# (正则, [来源组]) —— 命中即累加对应组
_RULES: list[tuple[str, list[str]]] = [
    # 代码/技术类 → GitHub + StackOverflow
    (r"\bapi\b|报错|error|exception|异常|\bbug\b|崩溃|安装失败|"
     r"\bpip\b|\bnpm\b|\bsdk\b|函数|traceback|编译|报错信息|怎么实现|如何实现",
     ["code", "web"]),
    # 论文/学术 → arXiv
    (r"论文|paper|arxiv|文献|综述|算法原理|state[- ]of[- ]the[- ]art|\bsota\b",
     ["academic", "web"]),
    # B站/视频/教程 → bilibili + web
    (r"b站|bilibili|up主|番剧|鬼畜|视频|教程|教学|实况|演示|怎么弄|怎么做",
     ["video", "web"]),
    # 游戏/攻略 → wiki + bilibili + web（手游再加 taptap 由用户显式指定）
    # 关键词含高信号 gacha/RPG 词汇——光靠"游戏/攻略"会漏掉只含游戏名+术语的查询
    # （如"原神 钟离 配队"）；纯游戏名/角色名无法靠关键词识别，需用户显式 sources=game
    (r"游戏|手游|网游|端游|攻略|玩法|打法|构筑|加点|角色|关卡|\bboss\b|steam|"
     r"公测|内测|开服|抽卡|卡池|池子|复刻|阵容|配队|出装|连招|干员|培养|养成|"
     r"圣遗物|光锥|遗器|命座|星魂|天赋|速通|速刷|材料|突破|平民|强度|节奏榜|tier",
     ["wiki", "video", "web"]),
    # 实时/资讯类 → web
    (r"汇率|天气|股价|股票|价格|多少钱|新闻|实时|最新|今天|现在|发布会|官宣",
     ["web"]),
]


def route(query: str) -> list[str]:
    """根据 query 返回来源组列表（已去重、保序）。"""
    q = query.lower()
    groups: list[str] = []
    for pattern, grps in _RULES:
        if re.search(pattern, q, re.IGNORECASE):
            for g in grps:
                if g not in groups:
                    groups.append(g)
    return groups or ["web"]
