"""对话型 / 自省类工具：

- ``ask_user``    主动追问主人（弹窗）
- ``audit_query`` 自己查看刚才用了哪些工具（只读自己当前对话）

让有希在意图不明 / 多个可行方案时，**暂停**当前轮、把问题弹给用户、
等用户在前端选项中点一个 / 或自由作答，再继续。

实现要点：
- 工具是 ``async``，内部 ``await asyncio.Future``
- 通过 ``config["_event_emitter"]`` 把 ask_user 事件推到 server SSE 流
- server 路由 ``POST /api/ask_user/{ask_id}`` 接收用户答案后 ``Future.set_result``
- 10 分钟超时兜底（用户关页面 / 不答时不让 agent 永远卡住）

如果当前不是 web 场景（emitter 未注入），降级为提示"自然语言追问"。
"""
from __future__ import annotations

import asyncio
import uuid

from ai_agent import tool

# 全局待回答队列：ask_id -> asyncio.Future
# server.py 收到用户答案后查这个 dict，set_result 让工具继续
_PENDING_ASKS: dict[str, "asyncio.Future"] = {}

# 默认等待用户回答的超时（秒）。10 分钟内不答就放有希自己判断走。
ASK_TIMEOUT_SECONDS = 600


def get_pending_future(ask_id: str) -> "asyncio.Future | None":
    return _PENDING_ASKS.get(ask_id)


async def require_master_approval(
    action_summary: str,
    config: dict,
) -> tuple[bool, str]:
    """子对话受限模式下：弹窗向主人请求批准某个破坏性操作。

    什么时候调（业务规则）：
    - 当前对话是 ``sub`` 且 ``sub_level == "restricted"``
    - 工具是破坏性 / 全局影响（update/merge/forget_memory、define/delete/restore_skill 等）

    返回 ``(approved, raw_answer)``。``approved=True`` 表示主人选择"批准"；
    ``False`` 表示拒绝 / 超时 / emitter 不可用。
    """
    question = (
        f"⚠️ 当前是**受限模式子对话**，有希请求执行破坏性 / 全局操作：\n\n"
        f"{action_summary}\n\n"
        f"批准让她继续吗？"
    )
    answer = await _prompt_user(question, ["✓ 批准", "✗ 拒绝"], config)
    if answer is None:
        return False, "(超时未答 / 当前不是 web 场景)"
    approved = ("批准" in answer) or ("✓" in answer) or ("yes" in answer.lower())
    return approved, answer


def is_restricted_sub(config: dict) -> bool:
    """判断当前对话是否是受限模式的子对话。"""
    cfg = (config or {}).get("configurable", {}) if config else {}
    return (cfg.get("conv_kind") == "sub") and (cfg.get("sub_level") == "restricted")


def is_sub(config: dict) -> bool:
    """判断当前对话是否是子对话（不分 level）。"""
    cfg = (config or {}).get("configurable", {}) if config else {}
    return cfg.get("conv_kind") == "sub"


def _normalize_string_list(value) -> list[str]:
    """把 LLM 可能乱传的 options / choices 字段规范化成 ``list[str]``。

    LLM 经常传错类型：
    - list[str] → 正确，直接用
    - 字符串 ``"A、B、C"`` → 经典陷阱：``list("ABC")`` 会拆字符，弹窗里每个
      字符都成了独立选项（"单个字符 / 标点作选项" 就是这么来的）。这里按
      常见分隔符（、；;|,，\\n）拆开。
    - JSON 字符串 ``'["A","B"]'`` → 用 json.loads 还原
    - None / 空 → 返回 []

    任何无法识别的类型都返回空列表（不阻塞主流程）。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        # 1) 尝试 JSON parse（LLM 偶尔会传 `'["A","B"]'` 字符串）
        if s.startswith("[") and s.endswith("]"):
            try:
                import json as _json
                parsed = _json.loads(s)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        # 2) 按常见分隔符拆
        for sep in ("、", "；", ";", "|", ",", "，", "\n"):
            if sep in s:
                return [p.strip() for p in s.split(sep) if p.strip()]
        # 3) 单一选项（整串视作一个）
        return [s]
    # 其他类型（dict / int 等）→ 空
    return []


async def _prompt_user(
    question: str,
    options: list[str],
    config: dict,
    groups: list[dict] | None = None,
) -> str | None:
    """内部辅助：向 server SSE 推 ask_user 事件 + 等用户答案。

    给 ask_user / request_pip_install 等"需要主人决策"的工具共用。
    返回用户答案字符串；emitter 缺失 / 超时返回 None。

    Args:
        groups: 多组独立小问题。每条形如 ``{"label": "TTS", "choices": [...]}``。
                有 groups 时前端按组分别渲染选项；用户每组各选一个，提交时
                聚合成一个多行字符串返回。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    emitter = cfg.get("_event_emitter")
    if emitter is None:
        return None

    ask_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _PENDING_ASKS[ask_id] = fut

    # 防御性：即便上层没规范化，也不能让 list(str) 把字符串拆字符
    safe_options = options if isinstance(options, list) else _normalize_string_list(options)
    safe_groups = groups if isinstance(groups, list) else []
    try:
        await emitter({
            "type": "ask_user",
            "id": ask_id,
            "question": question,
            "options": safe_options,
            "groups": safe_groups,
        })
        return await asyncio.wait_for(fut, timeout=ASK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return None
    finally:
        _PENDING_ASKS.pop(ask_id, None)


@tool
def audit_query(
    last_n: int = 20,
    tool_filter: str = "",
    config: dict = {},
) -> str:
    """查看你**当前对话**最近的工具调用审计日志（自己看自己刚才做了什么）。

    用途：
    - 主人问"你刚才调了哪些工具" / "中间报了什么错" → 查给主人看
    - 你想**反思**刚才的尝试（试了几次、哪步失败、能不能换路径）
    - 主人说"那个 xxx 是不是改错了" → 看 update_memory / edit_file 调用记录

    什么时候**不**用：
    - 闲聊 / 普通问题（多余）
    - 你刚做完一步立刻自查（你自己肯定记得，看日志只浪费 token）

    范围限制：只能看**当前对话**的 audit.jsonl（不能跨 thread，主人隐私保护）。

    参数：
        last_n: 返回最近多少次工具调用（默认 20，最多 100）。
                注：一次调用在日志里是 before+after 两条，工具自动配对。
        tool_filter: 只筛选包含某关键字的工具名（如 "memory" / "vision_describe" /
                     "edit_file"），留空显示全部。

    返回：格式化列表，每行一次完整调用：``[时间] tool(args) → ✓/✗ Nms 结果预览``
    """
    from audit import read_audit

    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")

    n = max(1, min(int(last_n or 20), 100))
    # 读多一点（每次调用 2 条事件），后面再 trim
    raw = read_audit(thread_id, limit=n * 4)
    if tool_filter:
        kw = tool_filter.strip().lower()
        raw = [r for r in raw if kw in str(r.get("tool", "")).lower()]

    # 按 tool_call_id 配对 before/after
    pairs: dict[str, dict] = {}
    order: list[str] = []
    for r in raw:
        tcid = r.get("tool_call_id") or f"_no_id_{len(order)}"
        if tcid not in pairs:
            pairs[tcid] = {}
            order.append(tcid)
        phase = r.get("phase")
        if phase in ("before", "after"):
            pairs[tcid][phase] = r

    # 只保留最近 n 个
    order = order[-n:]
    if not order:
        return "（本对话没有匹配的审计记录）"

    lines: list[str] = []
    for tcid in order:
        pair = pairs[tcid]
        b = pair.get("before") or {}
        a = pair.get("after") or {}
        ts = (b.get("ts") or a.get("ts") or "").split("T")[-1][:8]  # HH:MM:SS
        tool = b.get("tool") or a.get("tool") or "?"
        args = b.get("args") or {}
        # 简短 args 摘要
        if isinstance(args, dict):
            kvs = []
            for k, v in list(args.items())[:3]:
                vs = repr(v) if not isinstance(v, str) else f"{v!r}"
                if len(vs) > 40:
                    vs = vs[:37] + "...'"
                kvs.append(f"{k}={vs}")
            args_str = ", ".join(kvs)
        else:
            args_str = str(args)[:60]
        if a:
            ok = "✓" if a.get("ok") else "✗"
            ms = a.get("duration_ms", "?")
            preview = (a.get("result_preview") or "").replace("\n", " ")[:80]
            lines.append(f"[{ts}] {tool}({args_str}) → {ok} {ms}ms · {preview}")
        else:
            lines.append(f"[{ts}] {tool}({args_str}) → (未完成/未记录 after)")
    return "\n".join(lines)


@tool
def audit_stats(
    last_n: int = 200,
    config: dict = {},
) -> str:
    """**自查工具使用画像**：调用次数 / 成功率 / 平均耗时 / Top 失败工具。

    用途:
    - 自我监控: "我最近调啥多 / 哪个工具最常失败"
    - 主人问"你工具用得效率如何" / "最常踩什么坑"
    - 决定要不要 self_edit 优化某个工具（持续高失败率 = 该改）
    - 隔一段时间主动跑一次（self_edit_file 提示你 20 次后建议自查）

    Args:
        last_n: 统计最近多少次工具调用（默认 200，最多 1000）。

    返回: 表格化摘要 + Top 5 失败工具。
    """
    from audit import read_audit

    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")
    n = max(10, min(int(last_n or 200), 1000))
    raw = read_audit(thread_id, limit=n * 4)  # before+after 配对 → 多读些

    # 按 tool_call_id 配对
    pairs: dict[str, dict] = {}
    order: list[str] = []
    for r in raw:
        tcid = r.get("tool_call_id") or f"_no_id_{len(order)}"
        if tcid not in pairs:
            pairs[tcid] = {}
            order.append(tcid)
        phase = r.get("phase")
        if phase in ("before", "after"):
            pairs[tcid][phase] = r

    order = order[-n:]
    if not order:
        return "(本对话还没有审计记录)"

    # 按工具聚合
    by_tool: dict[str, dict] = {}
    total = 0
    total_ok = 0
    total_ms = 0
    completed = 0
    for tcid in order:
        pair = pairs[tcid]
        b = pair.get("before") or {}
        a = pair.get("after") or {}
        tool = b.get("tool") or a.get("tool") or "?"
        if tool not in by_tool:
            by_tool[tool] = {"calls": 0, "ok": 0, "ms_sum": 0, "ms_n": 0, "fail_examples": []}
        st = by_tool[tool]
        st["calls"] += 1
        total += 1
        if a:  # 有 after 算"完成"
            completed += 1
            if a.get("ok"):
                st["ok"] += 1
                total_ok += 1
            else:
                # 记一个失败示例
                if len(st["fail_examples"]) < 3:
                    preview = (a.get("result_preview") or "").replace("\n", " ")[:80]
                    st["fail_examples"].append(preview)
            ms = a.get("duration_ms")
            if isinstance(ms, (int, float)) and ms > 0:
                st["ms_sum"] += ms
                st["ms_n"] += 1
                total_ms += ms

    # 输出
    lines = [
        f"📊 工具调用统计 (最近 {len(order)} 次, 已完成 {completed})",
        "",
        f"总成功率: {total_ok}/{completed} = {total_ok*100/max(completed,1):.0f}%",
        f"总耗时: {total_ms/1000:.1f}s | 平均: {total_ms/max(completed,1):.0f}ms/次",
        "",
        "按工具：",
        f"{'工具':<25} {'调用':>5} {'成功率':>8} {'平均耗时':>10}",
        "─" * 55,
    ]
    # 按调用次数倒排
    sorted_tools = sorted(by_tool.items(), key=lambda kv: -kv[1]["calls"])
    for tool, st in sorted_tools[:15]:
        rate = st["ok"] / max(st["calls"], 1) * 100
        avg_ms = st["ms_sum"] / max(st["ms_n"], 1)
        emoji = "✓" if rate >= 90 else "⚠" if rate >= 70 else "✗"
        lines.append(
            f"{tool:<25} {st['calls']:>5} {emoji} {rate:>5.0f}%  {avg_ms:>7.0f}ms"
        )

    # Top 失败工具
    failing = [
        (tool, st) for tool, st in by_tool.items()
        if st["calls"] - st["ok"] > 0
    ]
    failing.sort(key=lambda kv: -(kv[1]["calls"] - kv[1]["ok"]))
    if failing:
        lines.append("")
        lines.append("Top 失败工具:")
        for tool, st in failing[:5]:
            fails = st["calls"] - st["ok"]
            lines.append(f"  ✗ {tool}: 失败 {fails}/{st['calls']}")
            for ex in st["fail_examples"][:2]:
                lines.append(f"     · {ex}")

    return "\n".join(lines)


@tool
async def ask_user(
    question: str,
    options: list[str] = None,
    groups: list[dict] = None,
    config: dict = None,
) -> str:
    """主动向主人提问，等他从前端选一个 / 输入自由答案。

    这是**会暂停你**的工具：调用后会等待主人在浮窗中回应，最多等 10 分钟。

    什么时候用：
    - 主人的指令**有歧义**（"那个文件" - 指哪个？）
    - 多个可行方案 / 路径，需要主人决定（"保存为 PDF 还是 Word？"）
    - 即将做较重 / 不可逆的操作前确认范围
    - 你不确定的关键事实

    什么时候**不要**用：
    - 你自己能合理推断的事（用 recall 拿用户偏好后能定 → 别问）
    - 一次性能查清楚的事实（先调 search / read_file → 别问）
    - 主人已经说清楚的事（重读上下文，别打断他）
    - **闲聊 / 寒暄**（很烦人）

    # 两种调用模式

    ## 模式 1：单一问题 → 用 ``options``
    一个明确的问题 + 一组候选答案。

      options=["统一成中文", "统一成英文", "保持原样不动"]

    ## 模式 2：多个独立小问题 → 用 ``groups``（重要！）

    一次要确认 **多件互不相关的事**时，**不要**把它们硬塞进单一 options
    （那样选项混在一起，主人看不清哪个选项属于哪个问题）。改用 groups：

      groups=[
        {"label": "TTS 引擎",  "choices": ["Edge TTS", "Azure TTS", "本地 Coqui"]},
        {"label": "API 格式",  "choices": ["OpenAI 兼容", "Anthropic 风格"]},
        {"label": "前端",      "choices": ["Web 页面", "Electron", "命令行"]},
      ]

    前端按组渲染（每组一行 label + 该组按钮一行），主人每组选一个，
    提交后聚合回答给你。返回形如：
      用户回答：
      - TTS 引擎: Edge TTS
      - API 格式: OpenAI 兼容
      - 前端: Web 页面

    判断方法：**问题之间相互独立** = 用 groups；**只是一个问题的多个候选** = 用 options。

    # 使用要点

    - **question**：写清楚问题/上下文，不要光说"你想怎么做"
    - **options 或 groups 二选一**：不要同时填（同时填以 groups 优先）
    - **同一轮最多调一次 ask_user**，连续追问会让用户烦

    参数：
        question: 总问题描述 / 上下文（必填）
        options: 单一问题模式 —— 一组候选答案列表
        groups:  多问题模式 —— 一组小问题，每项 {"label": str, "choices": list[str]}

    返回：
        用户的回答（已 prefix"用户回答："）。groups 模式时返回多行。
        超时返回兜底提示，按合理默认继续，不要再问。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    if cfg.get("_event_emitter") is None:
        return (
            "[ask_user 不可用：当前不是 web 场景，无法弹窗追问。"
            "请改为在文本回复里直接问主人。]"
        )

    # ── 规范化 options ──
    # 经典陷阱：LLM 传 options="选项A、选项B" 时，原代码 list(options) 会
    # 把字符串拆成单字符（每个汉字 / 标点都成独立选项），就是"单字符选项"
    # bug 的来源。改用 _normalize_string_list 兼容 list / str+分隔符 / JSON。
    options_was_str = isinstance(options, str)
    norm_options = _normalize_string_list(options)

    # ── 规范化 groups：丢弃非法项；每项必须有 label + choices ──
    norm_groups: list[dict] = []
    groups_had_str_choices = False
    for g in (groups or []):
        if not isinstance(g, dict):
            continue
        label = str(g.get("label", "")).strip()
        raw_choices = g.get("choices")
        if isinstance(raw_choices, str):
            groups_had_str_choices = True
        choices = _normalize_string_list(raw_choices)
        if not label or not choices:
            continue
        norm_groups.append({"label": label, "choices": choices})

    answer = await _prompt_user(question, norm_options, config, groups=norm_groups)
    if answer is None:
        return (
            "[用户在 10 分钟内未回答 —— 请按你的最佳判断继续，"
            "不要再次调用 ask_user。]"
        )

    # 如果触发了字符串→列表的兼容转换，提醒 LLM 下次直接传 list
    hint = ""
    if options_was_str or groups_had_str_choices:
        hint = (
            "\n\n[系统提示：你这次 ask_user 把 options/choices 传成了字符串"
            "（如 \"A、B、C\"），已自动按分隔符拆开。**下次请直接传 list**"
            "（如 [\"A\", \"B\", \"C\"]），避免单字符被拆成独立选项的 bug。]"
        )
    return f"用户回答：{answer}{hint}"


@tool
async def request_pip_install(
    package: str,
    version: str = "",
    reason: str = "",
    config: dict = {},
) -> str:
    """请求主人手动 ``pip install`` 一个 Python 包。**你不会真的去装** ——
    只是弹窗告诉主人 + 等他在终端跑完后告诉你"装好了"。

    什么时候用：
    - execute_code 报 ``ModuleNotFoundError: No module named 'xxx'``
    - 你识别到要做某事必须额外装包（如处理 webp 要 Pillow、画力学图要 sympy）

    什么时候**不要**用：
    - 先确认主人**确实没装** —— 试着 import 一下看真实报错
    - 已预装的库（pandas/openpyxl/numpy/matplotlib/python-docx/pdfplumber/
      Pillow/seaborn/reportlab/httpx/requests/bs4 等）不要请求
    - 仅为"以后可能用"装 —— 等真需要再请求

    流程：
    1. 你调本工具 → 主人收到弹窗 [我装好了] [拒绝（换思路）] [先放着]
    2. 主人点"我装好了" → 工具返回成功，**你重试** execute_code
    3. 主人点"拒绝" → 你**别再请求同一个包**，换实现思路（用已有库 / 简化逻辑）
    4. 主人点"先放着" 或 10 分钟没回应 → 当作"暂缓"，告诉主人结果可以晚点继续

    参数：
        package: 包名（如 ``pillow``、``openpyxl``）。**禁止**含路径 / URL / git+ 之类
        version: 可选具体版本号（如 ``"10.0.0"``）。强烈建议指定版本避免装最新版翻车
        reason: 简短说明为什么需要这个包（让主人评估是否值得装）

    返回：主人的决策结果文字。
    """
    import re as _re
    pkg = (package or "").strip()
    # 严格校验：只允许标准 pip 包名字符（字母 / 数字 / -_./[]）
    if not pkg or not _re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-\.\[\]]*$", pkg):
        return f"package 名格式非法（仅允许标准 PyPI 包名）：{package!r}"
    ver = (version or "").strip()
    if ver and not _re.match(r"^[A-Za-z0-9_\-\.\+]+$", ver):
        return f"version 格式非法：{version!r}"

    spec = f"{pkg}=={ver}" if ver else pkg
    cmd = f"pip install {spec}"
    reason_txt = (reason or "").strip()

    cfg = (config or {}).get("configurable", {}) if config else {}
    if cfg.get("_event_emitter") is None:
        return (
            f"[当前不是 web 场景无法弹窗] 请告诉主人手动跑：{cmd}"
            + (f"（原因：{reason_txt}）" if reason_txt else "")
        )

    question = (
        f"我需要 Python 包 `{spec}`"
        + (f"，用来：{reason_txt}。" if reason_txt else "。")
        + f"\n\n你方便的话在项目 .venv 终端跑：\n\n    {cmd}\n\n装好后告诉我可以重试。"
    )
    options = ["我装好了", "拒绝（让我换思路）", "先放着"]

    answer = await _prompt_user(question, options, config)
    if answer is None:
        return (
            f"[主人 10 分钟未回应安装 {spec} 的请求] 当作暂缓。"
            "告诉主人你需要这个包但他暂未确认；做点其他能做的，或等他回复后再继续。"
        )

    ans = answer.strip()
    if "装好了" in ans or "已装" in ans.lower() or "installed" in ans.lower():
        return f"主人已安装 `{spec}`。请重试你刚才失败的步骤。"
    if "拒绝" in ans:
        return (
            f"主人拒绝安装 `{spec}`。**不要再请求同一个包**，换实现思路："
            "用已预装的库重新实现，或者降级到不依赖该包的方案。如果真的没法绕开，"
            "告诉主人原因后等他决定。"
        )
    if "放着" in ans:
        return (
            f"主人暂时不装 `{spec}`。先做其他能做的工作，或者告诉主人当前阻塞点。"
        )
    # 自由作答兜底
    return f"主人回应：{ans}。请据此判断如何继续。"
