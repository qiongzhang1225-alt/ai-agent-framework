"""AI Agent 入口（瘦身版）。

职责：
- 加载持久化技能 + 注册所有内置工具（``import tools`` 触发）
- 拼装最终 SYSTEM_PROMPT（角色卡 prompts/yuki.md + 核心宪法 prompts/constitution.md
  + 常驻能力 prompts/core.md + 按需领域手册 prompts/playbooks/*.md）
- 提供 ``create_agent(model)`` 工厂（把核心宪法作为 reinject_prompt 传给 loop，
  长对话末尾自动重注，对抗规则漂移）
- 提供 ``__main__`` 终端 CLI

工具实现拆分在 ``tools/`` 目录；路径常量在 ``paths.py``。
"""
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from dotenv import load_dotenv

from ai_agent import Agent, DeepSeekClient, Message
# 触发 tools/*.py 的 @tool 装饰器执行，把内置工具注册到 ai_agent.tools._REGISTRY
# 同时 tools.skills 会扫描并加载 skills/*.py 持久化技能
import tools  # noqa: F401
from paths import PROJECT_ROOT, PROMPTS_DIR

load_dotenv()


# ── Prompt 加载 ───────────────────────────────────────────────────────────────


def _load_persona() -> str:
    """加载角色卡（prompts/yuki.md），找不到时返回空字符串（降级到纯技术 prompt）。"""
    try:
        return (PROMPTS_DIR / "yuki.md").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _load_constitution() -> str:
    """加载核心宪法（prompts/constitution.md）。

    这段**任何时候都适用**，且会作为 ``reinject_prompt`` 传给 loop —— 长对话 /
    长工具循环时自动重注到对话末尾，对抗开头 prompt 被稀释导致的规则遗忘。
    找不到时返回空字符串（降级，不阻断启动）。
    """
    try:
        return (PROMPTS_DIR / "constitution.md").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _load_core_prompt() -> str:
    """加载常驻能力 prompt（prompts/core.md）。

    core.md 是从旧 system.md 拆分出来的「始终加载」部分；领域重的章节
    （Unity / 自我编辑 / 视觉 UI / 记忆）已抽到 prompts/playbooks/ 按需加载。
    """
    return (PROMPTS_DIR / "core.md").read_text(encoding="utf-8").strip()


# ── 领域手册按需加载（MoE 式稀疏路由）────────────────────────────────────────
# domain -> (关键词触发, 工具名子串触发, 文件名)。命中（最近若干条消息文本含
# 关键词，或最近调过相关工具）才把该手册正文拼进 prompt，避免不相关领域文档
# 常驻、稀释核心准则。手册正文在 prompts/playbooks/ 下。
_PLAYBOOKS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "memory": (
        ("记住", "忘掉", "忘记", "我喜欢", "我讨厌", "我的偏好", "偏好",
         "记忆", "回收站", "撤销", "恢复刚", "remember", "recall"),
        ("remember", "recall", "memory", "forget", "merge_memories",
         "restore_memory", "restore_skill", "list_trash"),
        "memory.md",
    ),
    "ui_vision": (
        ("看图", "这张图", "图片", "截图", "视觉", "css", "样式", "主题",
         "前端", "配色", "界面", "排版", "参考图", "设计图"),
        ("vision_describe", "vision_check", "screenshot_and_describe"),
        "ui_vision.md",
    ),
    "self_edit": (
        ("改你自己", "改你的代码", "你的代码", "自我优化", "自我修改",
         "优化你的", "你的核心循环", "改你的 prompt", "改你的提示词"),
        ("self_edit", "self_read_file", "self_write_file", "self_rollback", "self_diff"),
        "self_edit.md",
    ),
    "unity": (
        ("unity", "场景", "gameobject", "游戏对象", "prefab", "预制体", "c#"),
        ("unity", "manage_scene", "manage_gameobject", "find_gameobjects",
         "manage_prefab", "manage_components", "mcp_reload", "manage_editor"),
        "unity.md",
    ),
    "debug_native": (
        ("段错误", "闪退", "崩溃", "segfault", "access violation",
         "0xc0000005", "3221225477", "exit 139", "退出码", "faulthandler",
         "unicodeencodeerror", "乱码", "gbk", "启动失败", "原生崩溃",
         "微调", "训练脚本"),
        ("run_command_stream",),
        "debug_native.md",
    ),
}

# 扫描最近多少条消息来决定加载哪些手册（兼顾"当前请求触发"+"近期工具粘性"）
_PLAYBOOK_SCAN_WINDOW = 10


# ── 共享路由骨架 ─────────────────────────────────────────────────────────────
# playbook 路由、工具路由、工作流路由都吃同一套信号：扫最近若干条消息，
# proactive（关键词出现在文本）+ sticky（近期调过相关工具）。抽出来三方共用。

def _recent_signal(conv: dict | None, window: int = _PLAYBOOK_SCAN_WINDOW) -> tuple[str, list[str]]:
    """从最近 ``window`` 条消息提取 (小写文本 blob, 小写工具名列表)。无消息返回 ("", [])。"""
    messages = (conv or {}).get("messages") or []
    recent = messages[-window:]
    text_blob = ""
    tool_names: list[str] = []
    for m in recent:
        text_blob += " " + (m.get("content") or "")
        for tc in (m.get("tool_calls") or []):
            tool_names.append((tc.get("name") or "").lower())
    return text_blob.lower(), tool_names


def _route_hit(
    keywords: tuple[str, ...] | list,
    tool_subs: tuple[str, ...] | list,
    text_blob: str,
    tool_names: list[str],
) -> bool:
    """命中判定：关键词出现在文本（proactive）或近期工具名含子串（sticky）。"""
    if any(str(kw).lower() in text_blob for kw in keywords):
        return True
    return any(str(sub).lower() in name for name in tool_names for sub in tool_subs)


def _load_playbook(filename: str) -> str:
    try:
        return (PROMPTS_DIR / "playbooks" / filename).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _select_playbooks(conv: dict | None) -> str:
    """根据近期对话内容 / 工具使用，挑选要加载的领域手册，拼成一段。

    - proactive：最近用户消息出现关键词 → 加载（首次调用前就能就位）。
    - sticky：最近调过该领域工具 → 持续加载（让多步任务全程有手册）。

    conv 为 None（CLI / smoke 测试）或无消息时不加载任何手册，只靠核心宪法 +
    core.md。与核心准则冲突时仍以核心准则为准（header 里写明）。
    """
    if not conv:
        return ""
    text_blob, tool_names = _recent_signal(conv)
    if not text_blob.strip() and not tool_names:
        return ""

    selected: list[str] = []
    for _domain, (keywords, tool_subs, filename) in _PLAYBOOKS.items():
        if _route_hit(keywords, tool_subs, text_blob, tool_names):
            body = _load_playbook(filename)
            if body:
                selected.append(body)

    if not selected:
        return ""
    header = (
        "【领域手册（按当前任务自动加载）】下面是与当前任务相关的专项手册，"
        "细节以手册为准；与核心准则冲突时以核心准则为准。"
    )
    return header + "\n\n" + "\n\n---\n\n".join(selected)


# 工作流数量 ≤ 此阈值时全量注入索引（行很短、稀释可忽略）；超过才按任务路由。
# 接口已就位，到量自动切换，无需改调用方。
_WORKFLOW_ROUTE_THRESHOLD = 6


def _select_workflows(conv: dict | None) -> str:
    """工作流索引注入：和 ``_select_playbooks`` 平级的孪生路由（共享 _recent_signal/_route_hit）。

    与 playbook 的关键差异：触发信号**自派生**，不是查静态表 ——
    每个工作流的命中关键词 = 自写 ``triggers`` + 工作流名；sticky = 它各步引用的工具。
    （playbook 的关键词是人手写在 _PLAYBOOKS 里的，工作流没人手写，故从自身内容派生。）

    分级：
    - conv=None（CLI/smoke）或工作流数 ≤ 阈值 → 全量索引（现在就是这条路，2 个工作流）。
    - 超过阈值 → 只注入命中的；一个都没命中 → 一行指针兜底（保住可发现性，不稀释）。

    异常一律兜底全量，绝不让有希"看不见"自己的工作流。
    """
    try:
        from tools.workflow_store import (
            list_all,
            format_workflows_index,
            format_workflows_pointer,
        )
        items = list_all()
        if not items:
            return ""
        if conv is None or len(items) <= _WORKFLOW_ROUTE_THRESHOLD:
            return format_workflows_index(items, routed=False)

        text_blob, tool_names = _recent_signal(conv)
        matched = [
            w for w in items
            if _route_hit(
                tuple(w.get("triggers", [])) + (w["name"],),
                tuple(w.get("tools", [])),
                text_blob,
                tool_names,
            )
        ]
        if matched:
            return format_workflows_index(matched, routed=True)
        return format_workflows_pointer(len(items))
    except Exception:
        try:
            from tools.workflow_store import format_workflows_for_prompt
            return format_workflows_for_prompt()
        except Exception:
            return ""


def _crystallization_nudge(conv: dict | None) -> str:
    """优化 E 的**被动提议**：有希刚又重复了一个"出现过、尚未固化"的多工具套路时，
    贴一行提示她考虑 define_workflow / suggest_workflow。

    紧贴"最近一个任务"门控 —— 只在"刚又做了一遍"那一回合冒头，避免每轮唠叨；
    一旦固化（被现有工作流覆盖）即自动静默。窗口取最近 12 个任务，控成本。
    异常一律静默（自优化提示绝不能拖垮主流程）。
    """
    if not conv:
        return ""
    try:
        from tools.workflow_store import (
            extract_task_tool_sequences,
            find_crystallization_candidates,
            _collapse_runs,
            _CRYSTALLIZE_IGNORE,
            list_all,
        )
        seqs = extract_task_tool_sequences(conv)
        if len(seqs) < 2:
            return ""
        recent = seqs[-12:]
        covered = [set(w.get("tools", [])) for w in list_all()]
        cands = find_crystallization_candidates(recent, covered_tool_sets=covered)
        if not cands:
            return ""
        # 门控：候选套路必须出现在"刚结束的那个任务"里（= 刚又重复了一遍）
        last_clean = [t for t in _collapse_runs(seqs[-1]) if t not in _CRYSTALLIZE_IGNORE]

        def _contains(hay: list, needle: list) -> bool:
            ln = len(needle)
            return any(hay[i:i + ln] == needle for i in range(len(hay) - ln + 1))

        hot = [c for c in cands if _contains(last_clean, c["sequence"])]
        if not hot:
            return ""
        c = hot[0]
        arrow = " → ".join(c["sequence"])
        return (
            f"【可固化提醒】你刚又跑了一遍 `{arrow}`（已在 {c['count']} 个任务里出现）。"
            "若这是稳定打法，考虑用 `define_workflow` 固化、或调 `suggest_workflow` 看完整建议。"
        )
    except Exception:
        return ""


# ── 工具按需路由（与 playbook 同款稀疏路由，避免 63+ 工具全量稀释模型选择）──────
# 背景：prompt 早就按需路由了，但工具一直是全量绑定（create_agent 没传 tools）。
# 审计实测 63 个静态工具里 ~37% 从未被调用，模型反复退回少数通用工具，
# 专门工具（code_indexer / coding 质量工具等）被噪声淹没。这里把工具也路由起来。

# 常驻核心集：跨领域、几乎每个任务都可能用到，永远绑定。
_CORE_TOOLS: tuple[str, ...] = (
    "execute_code", "run_command", "run_command_stream",
    "read_file", "write_file", "edit_file", "grep", "glob",
    "search", "fetch_webpage", "get_current_datetime", "calculate",
    "recall", "remember", "ask_user",
    "todo_write", "todo_read",
    "plan_task", "verify_change", "write_postmortem",
    "define_skill",
    # 逃生阀：路由漏了某工具时，按描述检索并当场激活（见 tools/skills.py:search_tools）。
    # 必须常驻 —— 它是关键词门控失手时唯一的找回手段，缺它路由就从"稀疏"退化成"残缺"。
    "search_tools",
)

# 领域工具包：domain -> (关键词触发, 工具名子串触发[sticky], 该包工具名)。
# 命中（最近消息含关键词 / 近期调过相关工具）才把该包并入绑定集。
_TOOL_BUNDLES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "software": (
        ("代码", "函数", "重构", "bug", "报错", "测试", "lint", "编译",
         "import", ".py", "调试", "debug", "脚本", "索引", "微调", "训练"),
        ("lint", "run_tests", "smoke_test", "code_search", "code_outline",
         "code_references", "code_dependencies", "apply_patch",
         "find_references", "format_code"),
        ("code_search", "code_outline", "code_references", "code_dependencies",
         "lint", "format_code", "run_tests", "apply_patch", "find_references",
         "smoke_test"),
    ),
    "self_edit": (
        ("改你自己", "改你的代码", "你的代码", "自我优化", "自我修改",
         "优化你的", "你的核心循环", "改你的 prompt", "改你的提示词", "改自己"),
        ("self_edit", "self_read_file", "self_write_file", "self_rollback", "self_diff"),
        ("self_read_file", "self_edit_file", "self_write_file",
         "self_edit_with_test", "self_rollback", "self_diff",
         "lint", "smoke_test", "run_tests"),
    ),
    "memory": (
        ("记住", "忘掉", "忘记", "我喜欢", "我讨厌", "我的偏好", "偏好",
         "记忆", "回收站", "撤销", "恢复刚", "remember", "recall"),
        ("update_memory", "merge_memories", "forget_memory",
         "restore_memory", "list_trash"),
        ("update_memory", "merge_memories", "forget_memory",
         "restore_memory", "list_trash"),
    ),
    "vision_ui": (
        ("看图", "这张图", "图片", "截图", "视觉", "css", "样式", "主题",
         "前端", "配色", "界面", "排版", "参考图", "设计图"),
        ("vision_describe", "vision_check", "screenshot_and_describe"),
        ("vision_describe", "vision_check", "screenshot_and_describe"),
    ),
    "unity": (
        ("unity", "场景", "gameobject", "游戏对象", "prefab", "预制体", "c#"),
        ("unity", "mcp_unity", "manage_scene", "manage_gameobject",
         "find_gameobjects", "manage_components", "mcp_reload", "manage_editor"),
        ("setup_unity_mcp_bridge", "remove_unity_mcp_bridge",
         "detect_current_unity_project", "list_unity_projects",
         "mcp_reload", "mcp_connect_server", "mcp_disconnect_server"),
    ),
    "skills_mgmt": (
        ("技能", "skill", "保存为技能", "做个工具", "沉淀"),
        ("define_skill", "list_skills", "delete_skill", "restore_skill"),
        ("list_skills", "delete_skill", "restore_skill"),
    ),
    "subconv": (
        ("子对话", "开个对话", "新开一个", "并行", "另开"),
        ("spawn_sub_conversation", "complete_sub_conversation"),
        ("spawn_sub_conversation", "complete_sub_conversation"),
    ),
    "env": (
        ("venv", "虚拟环境", "装包", "安装包", "依赖", "pip ", "requirements"),
        ("venv_install", "create_venv", "request_pip_install"),
        ("venv_install", "create_venv", "request_pip_install"),
    ),
    "ops": (
        ("changelog", "更新日志", "审计", "调了什么", "audit",
         "覆盖率", "利用率", "路由", "盲区"),
        ("audit_query", "audit_stats", "regenerate_changelog", "routing_coverage"),
        ("audit_query", "audit_stats", "regenerate_changelog", "routing_coverage"),
    ),
}

# 所有"静态已知"工具名（用于区分动态注册的自定义技能 / MCP 工具）。
_KNOWN_STATIC_TOOLS: frozenset[str] = frozenset(
    _CORE_TOOLS + tuple(n for _, _, names in _TOOL_BUNDLES.values() for n in names)
)


def _select_tools(conv: dict | None) -> list:
    """按当前任务路由要绑定给 LLM 的工具子集，避免 63+ 工具全量稀释选择。

    - conv 为 None（CLI / smoke 测试）→ 返回全部工具，不路由。
    - 否则：常驻核心集 + 命中的领域包 + 所有自定义技能（动态注册，永远带）
      + MCP 工具（仅 unity 域命中时带，否则 42 个 mcp_unity_* 会撑爆预算）。

    任何异常 → 兜底返回全部工具（退化成改造前的全量行为，绝不让模型少工具）。
    每条消息 server 都新建 Agent → 本函数每条消息重跑，能感知新定义的技能 + 域切换。
    """
    from ai_agent.tools import list_tools
    all_tools = list_tools()
    if not conv:
        return all_tools
    try:
        text_blob, tool_names = _recent_signal(conv)

        selected: set[str] = set(_CORE_TOOLS)
        unity_active = False
        for _domain, (keywords, tool_subs, tool_list) in _TOOL_BUNDLES.items():
            if _route_hit(keywords, tool_subs, text_blob, tool_names):
                selected.update(tool_list)
                if _domain == "unity":
                    unity_active = True

        # 动态工具：自定义技能（用户亲手定义，高意图）永远带；
        # MCP 工具（mcp_ 前缀，单 Unity 项目就 42 个）仅 unity 域命中时带。
        for t in all_tools:
            if t.name in _KNOWN_STATIC_TOOLS:
                continue
            if t.name.startswith("mcp_"):
                if unity_active:
                    selected.add(t.name)
            else:
                selected.add(t.name)

        result = [t for t in all_tools if t.name in selected]
        return result or all_tools
    except Exception:
        return all_tools


def _format_approved_sub_summaries() -> str:
    """读所有 ``summary_approved_for_master=True`` 的子对话摘要拼成 prompt 段。

    仅 master 对话注入这个块（让有希持续看到所有已批准的子对话上下文）。
    """
    import json
    from paths import META_DIR

    if not META_DIR.exists():
        return ""

    items: list[tuple[str, str, str]] = []  # (updated_at, name, summary)
    for d in META_DIR.iterdir():
        if not d.is_dir():
            continue
        conv_path = d / "conv.json"
        if not conv_path.exists():
            continue
        try:
            data = json.loads(conv_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("kind") != "sub":
            continue
        if not data.get("summary_approved_for_master"):
            continue
        summary = (data.get("summary") or "").strip()
        if not summary:
            continue
        items.append((
            data.get("summary_updated_at", ""),
            data.get("name", "(未命名子对话)"),
            summary,
        ))
    if not items:
        return ""

    # 最近更新的在前，最多 10 条
    items.sort(reverse=True)
    lines = [
        "【主对话上下文 - 已批准的子对话摘要】",
        "下面是各个子对话的产出（主人已批准纳入主对话）。"
        "聊到相关话题时可以引用，但不要直接复述：",
    ]
    for upd, name, summary in items[:10]:
        truncated = summary[:600]
        lines.append(f"\n### {name}（{upd[:10] if upd else '?'}）\n{truncated}")
    return "\n".join(lines)


def _full_prompt(conv: dict | None = None) -> str:
    """拼装最终 SYSTEM_PROMPT。

    顺序：角色卡（人设）→ 核心宪法（任何时候都适用）→ 常驻能力 core.md →
    按需领域手册 playbooks/*.md（依当前任务命中）→ 当前自定义技能清单（动态生成）
    → 回滚感知（如有）→ 已批准的子对话摘要（仅 master）→ 全局必读记忆 → 本对话复盘。

    各段每次 create_agent 时实时计算 —— 新增/删除技能、主人 revert 改动、
    新批准子摘要、当前任务命中的手册 等都能在下次新对话/下条消息立即反映。

    Args:
        conv: 当前对话 dict（含 kind / messages 字段）。``kind == "master"`` 时注入
              子摘要块；``messages`` 用于领域手册路由。其他类型不注入子摘要
              （standalone / sub 独立运行）。
    """
    from tools.skills import format_skills_for_prompt
    from tools.self_edit import format_rollback_warnings_for_prompt
    from tools.postmortem import format_postmortems_for_prompt
    from tools.sub_complete import format_recent_sub_completions_for_prompt

    persona = _load_persona()
    constitution = _load_constitution()
    core = _load_core_prompt()
    playbooks_block = _select_playbooks(conv)
    workflows_block = _select_workflows(conv)  # 与 playbook 平级、相邻：两层 guidance
    crystallization_block = _crystallization_nudge(conv)  # 优化 E：刚重复套路时的固化提醒
    skills_block = format_skills_for_prompt()
    rollback_block = format_rollback_warnings_for_prompt()

    # 仅 master 对话注入两类子对话上下文
    sub_summary_block = ""
    sub_completion_block = ""
    if conv and conv.get("kind") == "master":
        sub_summary_block = _format_approved_sub_summaries()
        sub_completion_block = format_recent_sub_completions_for_prompt(limit=5)

    # 全局必读记忆（importance >= 9，跨对话自动注入）
    important_memories_block = ""
    try:
        from memory import get_important_memories
        imps = get_important_memories(min_importance=9, limit=5)
        if imps:
            lines = ["【全局必读记忆】", "以下是高优先级记忆（importance >= 9），每次对话自动注入："]
            for i, m in enumerate(imps, 1):
                cat_label = {"agent_directive": "行为指示", "user_profile": "用户偏好"}.get(m["category"], m["category"])
                lines.append(f"\n{i}. [{cat_label}]({m['importance']}/10)")
                lines.append(f"   {m['text']}")
            important_memories_block = "\n".join(lines)
    except Exception:
        pass  # 不影响主流程

    # 本对话历史复盘（per-thread postmortem）
    postmortem_block = ""
    if conv and conv.get("id"):
        postmortem_block = format_postmortems_for_prompt(str(conv["id"]))

    parts: list[str] = []
    if persona:
        parts.append(persona)
    if constitution:
        parts.append(constitution)
    parts.append(core)
    if playbooks_block:
        parts.append(playbooks_block)
    if workflows_block:
        parts.append(workflows_block)
    if crystallization_block:
        parts.append(crystallization_block)
    if skills_block:
        parts.append(skills_block)
    if rollback_block:
        parts.append(rollback_block)
    if sub_summary_block:
        parts.append(sub_summary_block)
    if sub_completion_block:
        parts.append(sub_completion_block)
    if important_memories_block:
        parts.append(important_memories_block)
    if postmortem_block:
        parts.append(postmortem_block)
    return "\n\n---\n\n".join(parts)


# ── Agent 工厂 ────────────────────────────────────────────────────────────────


def create_agent(model: str = "deepseek-v4-flash", conv: dict | None = None) -> Agent:
    """创建自建 Agent 实例，默认用 DeepSeek 主对话。

    主对话只用 DeepSeek（``deepseek-v4-flash`` / ``deepseek-v4-pro``）。
    视觉识别通过 ``vision_describe`` 工具独立调任意 OpenAI 兼容视觉
    端点路由链（``VISION_*`` 主力 → ``VISION_*_2`` 备用 → ``VISION_*_3`` 最强保底，
    自动故障转移），不暴露在主模型选择里。

    - reasoning_content 处理已内建到 ``Message.to_openai()``
    - tools 按当前任务稀疏路由（``_select_tools(conv)``：常驻核心集 + 命中领域包
      + 自定义技能；MCP 仅 unity 域带）。server 每条消息新建 Agent → 每条消息重路由。
    - 注入 audit logger 写 ``.sandbox/_meta/<tid>/audit.jsonl``
    - ``conv`` 传 master conv 时，prompt 注入已批准的子摘要

    server.py 现在每次 chat 都新建 Agent（共享 LLM client，重的是 prompt
    动态生成 —— 不重）；不再缓存 Agent 实例本身。
    """
    from audit import log_tool_event

    llm = DeepSeekClient(model=model, temperature=0.7)
    return Agent(
        llm=llm,
        tools=_select_tools(conv),
        system_prompt=_full_prompt(conv=conv),
        on_tool_event=log_tool_event,
        reinject_prompt=_load_constitution(),
    )


# ── 终端 CLI（直接 python agent.py 运行时使用）─────────────────────────────────

if __name__ == "__main__":
    import asyncio

    agent = create_agent()
    cli_workdir = PROJECT_ROOT / ".sandbox" / "workspace" / "cli"
    cli_workdir.mkdir(parents=True, exist_ok=True)
    print("=" * 50)
    print("  DeepSeek Agent 已启动（输入 quit 退出）")
    print(f"  工作目录: {cli_workdir}")
    print("=" * 50)

    history: list[Message] = []

    async def _chat_loop():
        cfg = {"thread_id": "main", "workdir": str(cli_workdir)}
        while True:
            try:
                user_input = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                return
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "退出", "q"):
                print("再见！")
                return
            history.append(Message.user(user_input))
            print("\nAgent: ", end="", flush=True)
            try:
                async for ev in agent.astream(history, config=cfg):
                    if ev["type"] == "delta":
                        print(ev["text"], end="", flush=True)
                    elif ev["type"] == "tool_call":
                        print(f"\n  [调用 {ev['name']}]", flush=True)
                    elif ev["type"] == "done":
                        history.extend(ev["new_messages"])
                        print()
                    elif ev["type"] == "error":
                        print(f"\n[错误] {ev['error']}")
                        break
            except Exception as e:
                print(f"\n[错误] {e}")

    asyncio.run(_chat_loop())
