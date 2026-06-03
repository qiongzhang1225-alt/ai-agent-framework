"""视觉识别工具：DeepSeek 调 MiMo 看图（orchestrator + specialist 模式）。

设计：
- 主对话由 DeepSeek 主导（不支持视觉）
- 用户上传图片时，server 把图存到 ``.sandbox/_meta/<tid>/images/<image_id>.<ext>``
  并在 user message 文本里加 ``[已上传图片：img_xxx]`` 占位
- DeepSeek 看到占位 → 主动调 ``vision_describe(image_id, question)``
- 本工具内部用 MiMoClient 调小米 MiMo 多模态 API 拿描述返回给 DeepSeek
- DeepSeek 整合描述回答用户

好处：
- DeepSeek 历史里永远是纯文本，不会再 400
- 用户**任意次**追问图片细节 → DeepSeek 都能再调一次 vision_describe
- 图片存盘不进 conv.json，避免 base64 撑爆历史
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from ai_agent import tool
from paths import DEFAULT_WORKDIR, META_DIR

# 图片存放在每个对话的 meta 子目录
IMAGES_SUBDIR = "images"

# 视觉模型可识别的扩展名（OpenAI 多模态规范支持的位图格式）
VISION_IMG_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif",
})

# 默认视觉模型（含图片输入能力）
DEFAULT_VISION_MODEL = "mimo-v2.5"


def _images_dir(thread_id: str) -> Path:
    return META_DIR / thread_id / IMAGES_SUBDIR


def find_image_path(thread_id: str, image_id: str) -> Path | None:
    """按 image_id 找上传的图片（在 ``.sandbox/_meta/<tid>/images/`` 下）。"""
    d = _images_dir(thread_id)
    if not d.exists():
        return None
    matches = list(d.glob(f"{image_id}.*"))
    return matches[0] if matches else None


def _resolve_workdir_image(workdir: Path, raw_path: str) -> Path | None:
    """在 workdir 里解析图片路径，必须落在 workdir 内（防越界）。

    raw_path 接受：
    - 相对路径（"chart.png" / "out/chart.png"）
    - workdir 内的绝对路径（"E:/.../workspace/<tid>/chart.png"）
    扩展名必须在 VISION_IMG_EXTS 里，且文件实际存在。
    """
    try:
        p = Path(raw_path)
        if not p.is_absolute():
            p = workdir / p
        p = p.resolve()
        # 越界保护
        try:
            p.relative_to(workdir.resolve())
        except ValueError:
            return None
        if not p.is_file():
            return None
        if p.suffix.lower() not in VISION_IMG_EXTS:
            return None
        return p
    except Exception:
        return None


def _call_mimo_sync(data_url: str, question: str, model: str = DEFAULT_VISION_MODEL) -> str:
    """urllib 同步调用 MiMo API（httpx 连接超时时的回退方案）。

    直接 POST JSON，非流式，返回完整回答文本。
    """
    import json as _json
    import os as _os
    import ssl as _ssl
    import urllib.request as _urllib
    from ai_agent.llm import MIMO_BASE

    key = _os.getenv("MIMO_API_KEY")
    if not key:
        raise ValueError("MIMO_API_KEY 未设置")

    payload = _json.dumps({
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "max_tokens": 2000,
    }).encode("utf-8")

    req = _urllib.Request(
        f"{MIMO_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    ctx = _ssl.create_default_context()
    resp = _urllib.urlopen(req, timeout=120, context=ctx)
    data = _json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"] or ""


def _image_to_data_url(path: Path) -> str:
    """把本地图片读成 base64 data URL，喂给 OpenAI 兼容多模态 API。

    AVIF 格式会被 Pillow 转为 PNG（MiMo 不支持 image/avif MIME）。
    """
    suffix = path.suffix.lower()
    if suffix == ".avif":
        from PIL import Image
        import io
        img = Image.open(path)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


@tool
async def vision_describe(
    image_ref: str,
    question: str,
    config: dict,
) -> str:
    """让视觉模型（MiMo）看图回答**具体问题**，返回详细的中文描述 / 答案。

    主对话是 DeepSeek（看不到图），所以你要看图就必须调本工具。
    支持**两种**图片来源：

    1. **用户上传的图片**（``[已上传图片：img_xxxxxxxx]`` 占位）：
       传 ``image_ref="img_xxxxxxxx"``
    2. **工作目录里的图片文件**（如 execute_code 生成的 .png、
       用户工作目录中已有的图）：
       传 ``image_ref="chart.png"`` 或 ``image_ref="out/result_v2.png"``
       （workdir 相对路径，必须是 .png/.jpg/.jpeg/.gif/.webp/.bmp）

    同一张图可任意次调本工具问不同问题。

    什么时候用：
    - user message 里出现 ``[已上传图片：img_xxxxxxxx]`` → 至少调一次拿大致描述
    - 你用 execute_code 画了 .png（matplotlib / seaborn / Pillow 输出），
      **想确认成品是否符合用户要求** → 调本工具看一眼
    - 用户后续追问图里**细节**（颜色 / 文字 / 位置 / 数量） → 再调，question 改具体
    - 不要凭印象瞎猜，直接调

    什么时候**不要**用：
    - 历史里没有占位、workdir 里也没图，用户问题和图无关
    - 用户上传的是 .xlsx / .pdf / .docx（用 read_file，不是 vision）

    参数：
        image_ref: 两种格式
                   · ``img_xxxxxxxx``（用户上传的图片 id，从 [已上传图片：...] 取）
                   · workdir 相对路径（``chart.png`` / ``out/x.jpg`` 等）
        question: 你想知道什么。**具体 + 中文**最有效：
                  好例："详细描述这张图，包括所有可见元素、文字、风格"
                  好例："图片左下角的红色标签上写了什么字？"
                  好例："折线图的峰值在哪个月？标轴单位是什么？"
                  坏例："这是什么"（太宽泛）

    返回：
        MiMo 的描述 / 答案（中文）。当作你"亲眼看到"的事实使用，
        用你自己的话总结回答用户 —— **不要直接贴整段描述**。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")

    image_ref = (image_ref or "").strip()
    if not image_ref:
        return "image_ref 不能为空"

    path: Path | None = None

    # 解析路径 1：img_xxxxxxxx 形式 → 用户上传的图片
    if image_ref.startswith("img_"):
        path = find_image_path(thread_id, image_ref)
        if path is None:
            # 容错：裸 id（不带 img_ 前缀）
            path = find_image_path(thread_id, image_ref[4:])

    # 解析路径 2：workdir 相对 / 绝对路径
    if path is None:
        workdir = Path(cfg.get("workdir") or str(DEFAULT_WORKDIR)).resolve()
        path = _resolve_workdir_image(workdir, image_ref)

    # 路径 3 兜底：再试一次 img_<image_ref>（万一有希忘了加 img_ 前缀）
    if path is None and not image_ref.startswith("img_") and "/" not in image_ref and "\\" not in image_ref:
        path = find_image_path(thread_id, f"img_{image_ref}")

    if path is None:
        return (
            f"找不到图片 {image_ref!r}。可能原因："
            f"(1) 用户上传的图 id 不对 → 看 user 消息里的 [已上传图片：img_xxxxxxxx] 占位；"
            f"(2) workdir 路径错或非支持格式（{', '.join(sorted(VISION_IMG_EXTS))}）；"
            f"(3) 用 glob('*.png') 或 read_file 先确认文件存在。"
        )

    question = (question or "").strip() or "请用中文详细描述这张图，包括所有可见元素、文字、颜色、风格。"

    # 调 MiMo
    try:
        from ai_agent import MiMoClient, Message
    except Exception as e:
        return f"无法导入 MiMoClient: {e}"

    try:
        data_url = _image_to_data_url(path)
    except Exception as e:
        return f"读取图片失败 ({path.name}): {e}"

    # 优先用 MiMoClient（httpx 异步流式），失败回退到 urllib 同步调用
    text: str | None = None
    error_msg: str | None = None

    # 方式 1：MiMoClient（httpx 流式）
    try:
        client = MiMoClient(model=DEFAULT_VISION_MODEL)
    except ValueError as e:
        error_msg = (
            f"MiMo 未配置：{e}。"
            "请在 .env 里加 MIMO_API_KEY=<your_key>。"
        )
    else:
        msgs = [Message(role="user", content=[
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": data_url}},
        ])]
        parts: list[str] = []
        try:
            async for ev in client.stream(msgs):
                t = ev.get("type")
                if t == "delta":
                    parts.append(ev.get("text", ""))
                elif t == "error":
                    error_msg = ev.get("error", "未知错误")
                    break
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
        if not error_msg:
            text = "".join(parts).strip()

    # 方式 2：urllib 同步回退（httpx 在某些网络环境下连接超时）
    if text is None and error_msg is not None:
        try:
            text = _call_mimo_sync(data_url, question)
            error_msg = None  # 回退成功，清除错误
        except Exception as e:
            error_msg = f"MiMo 调用失败（httpx + urllib 均失败）：{e}"

    if error_msg and text is None:
        return error_msg

    return (text or "").strip() or "（MiMo 返回空，可能图像无法识别）"
