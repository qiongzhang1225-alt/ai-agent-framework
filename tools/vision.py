"""视觉识别工具：DeepSeek 调任意 OpenAI 兼容视觉模型看图（orchestrator + specialist 模式）。

设计：
- 主对话由 DeepSeek 主导（不支持视觉）
- 用户上传图片时，server 把图存到 ``.sandbox/_meta/<tid>/images/<image_id>.<ext>``
  并在 user message 文本里加 ``[已上传图片：img_xxx]`` 占位
- DeepSeek 看到占位 → 主动调 ``vision_describe(image_id, question)``
- 本工具内部用 OpenAI 兼容协议调**任意视觉模型**拿描述返回给 DeepSeek
- DeepSeek 整合描述回答用户

视觉模型路由链（环境变量驱动，按优先级自动故障转移）：
- 主力 slot 1: ``VISION_BASE_URL`` / ``VISION_API_KEY`` / ``VISION_MODEL``
- 备用 slot 2+: ``VISION_BASE_URL_2`` / ``VISION_API_KEY_2`` / ``VISION_MODEL_2``
  （可继续 _3 / _4 …，按数字顺序排队）
- 调用按链顺序逐个试：主力报错 / 超时 / 返回空 → 自动切下一个
- ``vision_describe(escalate=True)`` → 跳过 slot 1，直接用更强的备用
  （让有希在主力答得不够好时手动升级）

推荐（中国大陆直连，省钱优先）：
  - 主力 智谱 GLM-4.6V-Flash（永久免费）:
      ``VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4`` + ``glm-4.6v-flash``
  - 备用 通义 Qwen3-VL-Flash（便宜）:
      ``VISION_BASE_URL_2=https://dashscope.aliyuncs.com/compatible-mode/v1`` + ``qwen3-vl-flash``
  - 最强保底 通义 Qwen3-VL-Plus（key 同 slot 2）: ``VISION_MODEL_3=qwen3-vl-plus``
其它兼容端点（OpenAI / 豆包 / 硅基流动 / OpenRouter / Ollama 本地）见 .env.example。

好处：
- DeepSeek 历史里永远是纯文本，不会再 400
- 用户**任意次**追问图片细节 → DeepSeek 都能再调一次 vision_describe
- 图片存盘不进 conv.json，避免 base64 撑爆历史
"""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from ai_agent import tool
from paths import DEFAULT_WORKDIR, META_DIR

# 图片存放在每个对话的 meta 子目录
IMAGES_SUBDIR = "images"

# 视觉模型可识别的扩展名（OpenAI 多模态规范支持的位图格式）
VISION_IMG_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif",
})


def _is_placeholder(v: str) -> bool:
    """判断一个值是不是 .env.example 的占位符（your_xxx_here 之类）。

    占位符视为"未配置"，避免假 key 污染路由链。
    """
    s = (v or "").strip().lower()
    return (not s) or s.startswith("your_") or s in {"changeme", "xxx", "todo"}


def _resolve_vision_chain() -> list[tuple[str, str, str]]:
    """解析视觉模型路由链，按优先级返回 [(base_url, api_key, model), ...]。

    顺序：
    1. slot 1：``VISION_BASE_URL`` / ``VISION_API_KEY`` / ``VISION_MODEL``
    2. slot 2..9：``VISION_BASE_URL_N`` / ``VISION_API_KEY_N`` / ``VISION_MODEL_N``

    每个 slot 需 base_url + model 齐全且 api_key 非占位符才入链。
    全空 → 抛 ValueError 引导主人配置。
    """
    chain: list[tuple[str, str, str]] = []

    def _slot(suffix: str) -> tuple[str, str, str] | None:
        bu = (os.getenv(f"VISION_BASE_URL{suffix}") or "").strip().rstrip("/")
        ak = (os.getenv(f"VISION_API_KEY{suffix}") or "").strip()
        mo = (os.getenv(f"VISION_MODEL{suffix}") or "").strip()
        if bu and mo and not _is_placeholder(ak):
            return (bu, ak, mo)
        return None

    # slot 1（无后缀）+ slot 2..9（_N 后缀），按数字顺序排队
    for suffix in [""] + [f"_{i}" for i in range(2, 10)]:
        s = _slot(suffix)
        if s and s not in chain:
            chain.append(s)

    if not chain:
        raise ValueError(
            "视觉模型未配置。在 .env 配主→备路由链（推荐，国内直连，免费/便宜）：\n"
            "  # 主力 智谱 GLM-4.6V-Flash（永久免费）\n"
            "  VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4\n"
            "  VISION_API_KEY=<在 bigmodel.cn 注册领取>\n"
            "  VISION_MODEL=glm-4.6v-flash\n"
            "  # 备用 通义 Qwen3-VL-Flash（便宜，主力挂了自动切）\n"
            "  VISION_BASE_URL_2=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "  VISION_API_KEY_2=<阿里云百炼 API Key>\n"
            "  VISION_MODEL_2=qwen3-vl-flash\n"
            "  # 最强保底 通义 Qwen3-VL-Plus（key 同 slot 2）\n"
            "  VISION_BASE_URL_3=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "  VISION_API_KEY_3=<同 VISION_API_KEY_2>\n"
            "  VISION_MODEL_3=qwen3-vl-plus\n"
            "任何 OpenAI 兼容视觉端点都行。"
        )
    return chain


def _resolve_vision_config() -> tuple[str, str, str]:
    """向后兼容：返回路由链的第一个（主力）provider。"""
    return _resolve_vision_chain()[0]


# 默认视觉模型（兼容老引用：解析失败时仍返字符串）
DEFAULT_VISION_MODEL = os.getenv("VISION_MODEL") or "glm-4.6v-flash"


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


def _call_vision_sync(
    provider: tuple[str, str, str], data_url: str, question: str
) -> str:
    """urllib 同步调用视觉端点（httpx 连接超时时的回退方案）。

    用传入的 ``provider``=(base_url, api_key, model) POST OpenAI 兼容的
    chat/completions 端点（非流式），返回完整回答文本。
    """
    import json as _json
    import ssl as _ssl
    import urllib.request as _urllib

    base_url, api_key, model = provider

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
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    ctx = _ssl.create_default_context()
    resp = _urllib.urlopen(req, timeout=120, context=ctx)
    data = _json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"] or ""


def _image_to_data_url(path: Path) -> str:
    """把本地图片读成 base64 data URL，喂给 OpenAI 兼容多模态 API。

    AVIF 格式会被 Pillow 转为 PNG（部分视觉模型不支持 image/avif MIME）。
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


async def _call_one_provider(
    provider: tuple[str, str, str], data_url: str, question: str
) -> tuple[str | None, str | None]:
    """调一个视觉 provider：httpx 流式优先，失败回退 urllib 同步。

    返回 (text, error)：
    - text 非空 → 成功
    - text == "" 且 error 为 None → 软失败（模型返回空，触发链下一个）
    - text 为 None 且 error 非空 → 硬失败（报错，触发链下一个）
    """
    base_url, api_key, model = provider
    label = f"{model}@{base_url}"

    try:
        from ai_agent import DeepSeekClient, Message
    except Exception as e:
        return None, f"{label}: 无法导入 DeepSeekClient: {e}"

    text: str | None = None
    error_msg: str | None = None

    # 方式 1：httpx 流式
    try:
        client = DeepSeekClient(model=model, api_key=api_key, api_base=base_url)
    except ValueError as e:
        error_msg = f"{label} 初始化失败：{e}"
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
                    error_msg = f"{label}: {ev.get('error', '未知错误')}"
                    break
        except Exception as e:
            error_msg = f"{label}: {type(e).__name__}: {e}"
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
            text = (_call_vision_sync(provider, data_url, question) or "").strip()
            error_msg = None
        except Exception as e:
            error_msg = f"{label} 调用失败（httpx + urllib 均失败）：{e}"

    return text, error_msg


@tool
async def vision_describe(
    image_ref: str,
    question: str,
    escalate: bool = False,
    config: dict = None,
) -> str:
    """让视觉模型看图回答**具体问题**，返回详细的中文描述 / 答案。

    主对话是 DeepSeek（看不到图），所以你要看图就必须调本工具。
    视觉模型走 .env 配置的**路由链**（主力 ``VISION_*`` → 备用 ``VISION_*_2``
    → 最强保底 ``VISION_*_3``）：主力报错 / 返回空会**自动切下一个**。
    支持任何 OpenAI 兼容视觉端点。

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
        escalate: 默认 False，用主力（免费 GLM）模型。若主力回答太模糊 /
                  答非所问 / 你怀疑它看错了 → 传 ``escalate=True`` 跳过主力，
                  直接用更强的备用模型（如 Qwen3-VL）重看一遍。

    返回：
        视觉模型的描述 / 答案（中文）。当作你"亲眼看到"的事实使用，
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

    # 读图为 data URL
    try:
        data_url = _image_to_data_url(path)
    except Exception as e:
        return f"读取图片失败 ({path.name}): {e}"

    # 解析路由链
    try:
        chain = _resolve_vision_chain()
    except ValueError as e:
        return str(e)

    # escalate：跳过主力（slot 1），直接用更强的备用（链 >1 时才生效）
    if escalate and len(chain) > 1:
        chain = chain[1:]

    # 按链顺序逐个尝试，第一个出非空结果即返回；
    # 报错 / 返回空 → 自动切下一个 provider（故障转移）
    errors: list[str] = []
    saw_empty = False
    for provider in chain:
        text, err = await _call_one_provider(provider, data_url, question)
        if text:
            # 附出处：让你知道是哪个模型答的，据此判断要不要 escalate 换更强的
            return f"{text}\n\n[路由：本次由 {provider[2]} 应答]"
        if err:
            errors.append(err)
        else:
            saw_empty = True

    if errors:
        return (
            "视觉识别失败，已按路由链尝试全部模型：\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    if saw_empty:
        return "（视觉模型返回空，可能图像无法识别或不支持该格式）"
    return "（视觉路由链为空，请检查 .env 的 VISION_* 配置）"


@tool
async def vision_check(config: dict = None) -> str:
    """视觉路由链自检：列出已配置的视觉模型，逐个发测试小图确认是否真能用。

    **不需要任何图片**，本工具自己造一张纯色小图去问每个模型。

    什么时候用：
    - 用户问"视觉 / 看图能不能用"、"视觉模型配好了吗"
    - 用户刚改完 .env 的 VISION_* 想验证
    - 你怀疑某个视觉模型挂了（vision_describe 一直走备用 / 一直失败）
    - 用户让你"主动路由 / 切换视觉模型"——先用本工具看清链上有哪几档、哪几档活着

    返回：每一档（主力 / 备用 / 最强保底）的 ✓ / ✗ 状态 + 失败原因。
    据此回答用户"能不能用"，以及决定 vision_describe 要不要带 escalate=True。
    """
    # 解析路由链（未配置直接把指引透传）
    try:
        chain = _resolve_vision_chain()
    except ValueError as e:
        return f"视觉路由链未配置：\n{e}"

    # 造一张 64x64 纯红测试图（Pillow 没装就退化成 1x1 内置 PNG）
    try:
        import io

        from PIL import Image

        img = Image.new("RGB", (64, 64), (210, 40, 40))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        q = "这张纯色图是什么颜色？只用一个中文词回答。"
    except Exception:
        png1x1 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgYPgPAAEEAQDk"
            "G0wdAAAAAElFTkSuQmCC"
        )
        data_url = "data:image/png;base64," + png1x1
        q = "用一个中文词描述这张图。"

    lines = [f"视觉路由链自检（共 {len(chain)} 档，主力在最前）："]
    ok_count = 0
    for i, provider in enumerate(chain, 1):
        base_url, _key, model = provider
        host = base_url.split("//", 1)[-1].split("/", 1)[0]
        role = "主力" if i == 1 else ("最强保底" if i == len(chain) else "备用")
        text, err = await _call_one_provider(provider, data_url, q)
        if text:
            ok_count += 1
            snippet = text.strip().replace("\n", " ")[:30]
            lines.append(f"  {i}. ✓ [{role}] {model} @ {host} — 答：\"{snippet}\"")
        else:
            reason = (err or "返回空").replace("\n", " ")[:80]
            lines.append(f"  {i}. ✗ [{role}] {model} @ {host} — {reason}")

    if ok_count == 0:
        lines.append("\n全部失败：检查 .env 的 key 是否正确、模型名是否对、网络是否通。")
    elif ok_count < len(chain):
        lines.append(
            f"\n{ok_count}/{len(chain)} 可用。挂掉的档会被自动跳过（故障转移），不影响看图。"
        )
    else:
        lines.append("\n全部可用。看图默认走主力；escalate=True 跳过主力用更强备用。")
    return "\n".join(lines)
