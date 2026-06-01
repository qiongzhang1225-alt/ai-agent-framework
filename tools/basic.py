"""基础工具：数学计算、当前时间、网络搜索、抓网页。"""
from __future__ import annotations

import ast
import datetime
import operator
import re
import urllib.request

import requests
from bs4 import BeautifulSoup

from ai_agent import tool


# ── 网络代理 ─────────────────────────────────────────────────────────────────


def _get_proxies() -> dict | None:
    """读取系统代理设置。返回 None 表示直连。"""
    p = urllib.request.getproxies()
    if p.get("http") or p.get("https"):
        return p
    return None


# ── calculate ────────────────────────────────────────────────────────────────

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def _safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"不支持的运算: {ast.dump(node)}")


@tool
def calculate(expression: str) -> str:
    """计算数学表达式，支持 + - * / ** % 运算。示例: '2 ** 10 + 3 * 4'"""
    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval(tree.body)
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算失败: {e}"


# ── get_current_datetime ─────────────────────────────────────────────────────

@tool
def get_current_datetime() -> str:
    """获取当前日期和时间。"""
    return datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")


# ── web_search ───────────────────────────────────────────────────────────────

_BING_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


@tool
def web_search(query: str) -> str:
    """搜索互联网，获取**实时信息**（Bing 后端）。

    什么时候必须用：
    - 用户问"今天 / 现在 / 最新 / 目前"的状态（天气、新闻、价格、版本号、汇率等）
    - 用户问任何具体日期 / 时段的事件、赛事结果
    - 用户问某个人 / 公司 / 产品的最近动态
    - 你不确定某个事实，或它在你的训练截止之后

    什么时候**不**用：
    - 纯数学 / 逻辑推理
    - 用户上传的文件内容分析
    - 代码相关问题（除非问"最新版本"之类）

    返回 5 条结果，每条含 title / url / snippet。
    """
    try:
        resp = requests.get(
            "https://cn.bing.com/search",
            params={"q": query},
            headers=_BING_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("li.b_algo")
        if not items:
            return f"未找到与 '{query}' 相关的结果"
        formatted = []
        for item in items[:5]:
            a_tag = item.select_one("h2 a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            cap = item.select_one(".b_caption p") or item.select_one(".b_lineclamp2")
            snippet = cap.get_text(strip=True) if cap else ""
            formatted.append(f"- {title}\n  {href}\n  {snippet}")
        return "\n\n".join(formatted) if formatted else f"未找到与 '{query}' 相关的结果"
    except Exception as e:
        return f"搜索失败: {e}"


# ── fetch_webpage ────────────────────────────────────────────────────────────

@tool
def fetch_webpage(url: str) -> str:
    """打开并读取指定 URL 的网页内容，适合查看具体链接的详细信息。"""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, proxies=_get_proxies())
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text()).strip()
        return text[:3000] + ("...(已截断)" if len(text) > 3000 else "")
    except Exception as e:
        return f"抓取失败: {e}"
