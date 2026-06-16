"""结果过滤：剔除词典/百科类污染（中文通用搜索时常见）。"""
from __future__ import annotations

from urllib.parse import urlparse

from .models import SearchResult

# 词典/百科/黄历类域名黑名单（这些结果通常无信息量）
DICT_DOMAINS = {
    # 百科（单字/词条释义，搜实时/游戏内容时纯噪声）
    "baike.baidu.com", "baike.so.com", "baike.sogou.com",
    # 词/字典
    "dict.youdao.com", "iciba.com", "hanyuguoxue.com",
    "gushici.net", "hancibao.com", "chagushici.com",
    "zdic.net", "dict.baidu.com", "hanyu.baidu.com",
    "mdbg.net", "cidian.qq.com", "xiexing.com",
    "hanzi.com", "zi.tools",
    # 老黄历/万年历（实测 cn.bing 常把"今天…"类查询污染成黄历站）
    "tthuangli.com",
}

# 标题特征：纯字词解释 / 老黄历（出现即判污染，跨域名兜底）
_DICT_TITLE_KW = (
    "_百度百科", "_360百科", "_搜狗百科",
    "的拼音", "的意思", "的解释", "怎么读",
    "_汉语", "部首", "笔顺", "新华字典",
    # 老黄历/万年历污染（"今日大连天气"被污染成的就是这类）
    "黄历", "黄道吉日", "宜忌",
)


def is_dict_result(r: SearchResult) -> bool:
    """判断一条结果是否为词典/百科类污染。"""
    domain = urlparse(r.url).netloc.lower()
    if any(d in domain for d in DICT_DOMAINS):
        return True
    if any(kw in r.title for kw in _DICT_TITLE_KW):
        return True
    return False


def filter_dict(results: list[SearchResult]) -> list[SearchResult]:
    """过滤掉词典/百科类结果。全被过滤时返回原列表（至少有点东西）。"""
    kept = [r for r in results if not is_dict_result(r)]
    return kept if kept else results
