"""screenshot_and_describe —— UI 类任务的自动反馈闭环工具。

设计动机（来自实际事故）：
用户让私人助手做白天主题，她改了 23 次 CSS。每次改完她**看不到**实际效果，
只能猜，所以反复堆补丁。这工具让她改完一步立刻：
1. 截当前页面 → workdir/.ui_check/<timestamp>.png
2. 调 MiMo vision_describe 看截图，对比她的预期
3. 不符合预期 → 改代码再截图，不需要等主人反馈

依赖 ``playwright`` + chromium。没装时返回明确提示让私人助手调
``request_pip_install`` 求主人安装。
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from ai_agent import tool
from paths import DEFAULT_WORKDIR


# 截图存放在工作目录的隐藏子文件夹
UI_CHECK_SUBDIR = ".ui_check"
DEFAULT_TARGET_URL = "http://127.0.0.1:3616/"
SCREENSHOT_VIEWPORT = {"width": 1440, "height": 900}
SCREENSHOT_TIMEOUT_MS = 15000


def _check_playwright() -> str | None:
    """检查 playwright 是否可用。可用返回 None；不可用返回引导文字。"""
    try:
        import playwright  # noqa: F401
        from playwright.async_api import async_playwright  # noqa: F401
        return None
    except ImportError:
        return (
            "❌ 截图依赖 playwright 但未安装。请告诉主人按顺序跑：\n"
            "  1. ``pip install playwright``（你可以调 request_pip_install('playwright')）\n"
            "  2. ``playwright install chromium``（主人手动跑，pip 不会自动触发）\n"
            "装好后再调本工具。"
        )


async def _take_screenshot(
    url: str,
    output_path: Path,
    selector: str | None = None,
    full_page: bool = False,
) -> str | None:
    """用 playwright headless chromium 截图。成功返回 None；失败返回错误。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:
            return (
                f"启动 chromium 失败：{e}。"
                f"主人可能没跑 ``playwright install chromium``。"
            )
        try:
            ctx = await browser.new_context(viewport=SCREENSHOT_VIEWPORT)
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=SCREENSHOT_TIMEOUT_MS)
            except Exception as e:
                # networkidle 在 SSE 流式后端可能永远不空闲，fallback 用 domcontentloaded
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=SCREENSHOT_TIMEOUT_MS)
                    # 给前端额外 1.5s 渲染时间
                    await asyncio.sleep(1.5)
                except Exception as e2:
                    return f"页面加载失败：{e2}（初始尝试：{e}）"

            if selector:
                el = await page.query_selector(selector)
                if not el:
                    return f"找不到 selector {selector!r}"
                await el.screenshot(path=str(output_path))
            else:
                await page.screenshot(
                    path=str(output_path),
                    full_page=full_page,
                )
            return None
        finally:
            try:
                await browser.close()
            except Exception:
                pass


@tool
async def screenshot_and_describe(
    url: str = DEFAULT_TARGET_URL,
    expectation: str = "",
    selector: str = "",
    full_page: bool = False,
    config: dict = None,
) -> str:
    """截当前 UI 截图 → 调 MiMo vision 看 → 对比 expectation 给反馈。

    **UI / 主题 / CSS 类任务的核心自检工具**。改完代码立刻调本工具，
    不要等主人反馈。MiMo 描述不符合预期 → 改代码再调，直到符合。

    什么时候用：
    - 你刚改完 ``static/style.css`` / ``templates/index.html`` 的视觉部分
    - 主人说"这里颜色不对" / "对比度差" 类反馈 → 自检确认问题在哪
    - 比对参考图调整设计（参考图描述塞进 expectation）
    - 验证主题切换、深浅色等是否生效

    什么时候**不要**用：
    - 改的不是视觉代码（逻辑 / 后端 / 工具实现）
    - 你已知改动结果（不需要看也确定对的）

    流程（自动）：
    1. playwright headless chromium 打开 url → 截图
    2. 存到 ``<workdir>/.ui_check/shot_<id>.png``
    3. 调 ``vision_describe`` 让 MiMo 看截图描述视觉效果
    4. 拼接 expectation + MiMo 描述返回给你，自己判断是否符合

    依赖：playwright + chromium。没装时工具返回明确引导让你调
    ``request_pip_install('playwright')``。

    参数：
        url: 要截图的页面（默认本地 ``http://127.0.0.1:3616/``）
        expectation: 你的设计预期（如"输入框文字暖灰色不是黑色，
            背景应该是低饱和粉紫"）。**强烈建议填**，否则 MiMo 不知道
            你的目标，反馈意义减半
        selector: 可选 CSS 选择器，只截某区域（如 ``".chat-input"``）
        full_page: True 截整页（包含滚动区），False（默认）只截 viewport

    返回：
        截图路径 + MiMo 视觉分析 + expectation 对比指引。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}

    # 1. 依赖检查
    dep_err = _check_playwright()
    if dep_err:
        return dep_err

    # 2. 确定截图保存路径
    workdir = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
    out_dir = workdir / UI_CHECK_SUBDIR
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"❌ 无法创建截图目录 {out_dir}：{e}"

    shot_id = uuid.uuid4().hex[:8]
    out_name = f"shot_{shot_id}.png"
    out_path = out_dir / out_name

    # 3. 截图
    target_url = (url or DEFAULT_TARGET_URL).strip()
    selector = (selector or "").strip() or None

    err = await _take_screenshot(target_url, out_path, selector=selector, full_page=full_page)
    if err:
        return f"❌ 截图失败：{err}"

    if not out_path.exists():
        return f"❌ 截图脚本未报错但文件不存在：{out_path}"

    # 4. 调 vision_describe（复用现有工具）
    expectation = (expectation or "").strip()
    if expectation:
        question = (
            "请详细描述这张 UI 截图的视觉效果，特别注意："
            "整体配色、文字可读性、对比度、布局协调感。"
            f"\n\n用户的设计预期是：\n{expectation}\n\n"
            "请**重点对比**实际效果是否符合这个预期，明确指出符合 / 不符合的具体方面。"
        )
    else:
        question = (
            "请详细描述这张 UI 截图：整体配色、文字颜色 / 可读性、"
            "对比度、布局、视觉协调感。如果有明显问题（如对比度不足、"
            "颜色不和谐、可读性差），明确指出。"
        )

    rel_path = out_path.relative_to(workdir).as_posix()

    try:
        from tools.vision import vision_describe as _vd
    except Exception as e:
        return f"❌ 截图成功但 vision_describe import 失败：{e}\n截图在：{rel_path}"

    # vision_describe 是 @tool，要从 _REGISTRY 拿；或者直接调 func
    try:
        from ai_agent.tools import get_tool
        vd_meta = get_tool("vision_describe")
        if vd_meta is None:
            return f"❌ vision_describe 工具未注册\n截图在：{rel_path}"
        vision_result = await vd_meta.func(
            image_ref=rel_path,
            question=question,
            config=config,
        )
    except Exception as e:
        return f"❌ vision_describe 调用失败：{type(e).__name__}: {e}\n截图在：{rel_path}"

    # 5. 拼装返回
    selector_note = f"（仅 {selector!r} 区域）" if selector else ""
    fullpage_note = "（整页含滚动）" if full_page else ""

    parts = [
        f"✓ 已截图 {out_name}{selector_note}{fullpage_note}",
        f"路径：``{rel_path}``",
        "",
        "### MiMo 视觉分析",
        vision_result.strip(),
    ]
    if expectation:
        parts += [
            "",
            "### 自检指引",
            "对比上面的描述和你的预期，**是否符合？**",
            "- 不符合 → **改代码后再次调用本工具**自检，不要立刻问主人",
            "- 你已经改 ≥ 3 次仍不符合 → 调 ``ask_user`` 求主人说具体哪不对",
            "- 符合 → 简短报告主人结果即可",
        ]
    else:
        parts += [
            "",
            "### 自检指引",
            "对比你的设计意图判断是否符合。**没提供 expectation 时建议下次填**，"
            "MiMo 没有目标无法精准对比，反馈意义减半。",
        ]
    return "\n".join(parts)
