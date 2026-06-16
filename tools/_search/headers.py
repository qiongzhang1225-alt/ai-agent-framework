"""共享 HTTP 头（统一 User-Agent，避免每个引擎各写一份）。"""
from __future__ import annotations

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

BASE_HEADERS = {"User-Agent": UA}


def with_referer(referer: str) -> dict:
    """返回带 Referer 的请求头（B站/TapTap 等需要）。"""
    return {"User-Agent": UA, "Referer": referer}
