"""搜索结果数据模型 + URL 归一化 + 文本截断。"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 跟踪类查询参数：去重时剥掉，避免同一页面被当成两条
_TRACKING_PREFIXES = ("utm_", "spm", "share_", "vd_", "ref_")
_TRACKING_KEYS = {
    "from", "from_source", "from_spmid", "seid", "ref", "referer",
    "from", "src", "source", "share_source", "share_medium",
    "buvid", "is_story_h5", "mid", "p", "plat_id",
}

SNIPPET_MAX = 200


@dataclass
class SearchResult:
    """一条统一格式的搜索结果。

    extra 放引擎特有的元数据（播放量 / star 数 / 评分等），
    格式化时拼成一行紧凑的 meta（无 emoji）。
    """

    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    rank: int = 0  # 引擎内原始排名（1 起）
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        self.url = (self.url or "").strip()
        self.snippet = truncate(self.snippet, SNIPPET_MAX)


def truncate(text: str, limit: int = SNIPPET_MAX) -> str:
    """折叠空白并截断到 limit 字符。"""
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def normalize_url(url: str) -> str:
    """URL 归一化，用作去重 key。

    - scheme/host 小写
    - 去掉 fragment（#...）
    - 剥掉跟踪类查询参数（utm_* / spm / from 等）
    - 去掉末尾多余的 /
    解析失败就原样返回（至少能按字符串去重）。
    """
    if not url:
        return ""
    try:
        parts = urlparse(url)
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/") or "/"
        kept = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() not in _TRACKING_KEYS
            and not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
        ]
        query = urlencode(kept)
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url.strip()
