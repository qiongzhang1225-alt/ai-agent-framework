"""AI Agent 入口（瘦身版）。

职责：
- 加载持久化技能 + 注册所有内置工具（``import tools`` 触发）
- 拼装最终 SYSTEM_PROMPT（角色卡 prompts/yuki.md + 技术约束 prompts/system.md）
- 提供 ``create_agent(model)`` 工厂
- 提供 ``__main__`` 终端 CLI

工具实现拆分在 ``tools/`` 目录；路径常量在 ``paths.py``。
"""
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from dotenv import load_dotenv

from ai_agent import Agent, DeepSeekClient, MiMoClient, Message
# 触发 tools/*.py 的 @tool 装饰器执行，把内置工具注册到 ai_agent.tools._REGISTRY
# 同时 tools.skills 会扫描并加载 skills/*.py 持久化技能
import tools  # noqa: F401
from paths import PROJECT_ROOT, PROMPTS_DIR

# model 路由：哪些 model id 用 MiMoClient（而非默认 DeepSeekClient）
MIMO_MODELS = frozenset({
    "mimo-v2.5", "mimo-v2.5-pro", "mimo-v2-omni", "mimo-v2-pro",
})

load_dotenv()


# ── Prompt 加载 ───────────────────────────────────────────────────────────────


def _load_persona() -> str:
    """加载角色卡（prompts/yuki.md），找不到时返回空字符串（降级到纯技术 prompt）。"""
    try:
        return (PROMPTS_DIR / "yuki.md").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _load_system_prompt() -> str:
    """加载技术约束 prompt（prompts/system.md）。"""
    return (PROMPTS_DIR / "system.md").read_text(encoding="utf-8").strip()


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

    顺序：角色卡（人设）→ 技术约束（能力边界）→ 当前自定义技能清单（动态生成）
    → 回滚感知（如有）→ 已批准的子对话摘要（仅 master）。

    各段每次 create_agent 时实时计算 —— 新增/删除技能、主人 revert 改动、
    新批准子摘要 等都能在下次新对话立即反映。

    Args:
        conv: 当前对话 dict（含 kind 字段）。``kind == "master"`` 时注入
              子摘要块；其他类型不注入（standalone / sub 独立运行）。
    """
    from tools.skills import format_skills_for_prompt
    from tools.self_edit import format_rollback_warnings_for_prompt
    from tools.postmortem import format_postmortems_for_prompt
    from tools.sub_complete import format_recent_sub_completions_for_prompt

    persona = _load_persona()
    system = _load_system_prompt()
    skills_block = format_skills_for_prompt()
    rollback_block = format_rollback_warnings_for_prompt()

    # 仅 master 对话注入两类子对话上下文
    sub_summary_block = ""
    sub_completion_block = ""
    if conv and conv.get("kind") == "master":
        sub_summary_block = _format_approved_sub_summaries()
        sub_completion_block = format_recent_sub_completions_for_prompt(limit=5)

    # 本对话历史复盘（per-thread postmortem）
    postmortem_block = ""
    if conv and conv.get("id"):
        postmortem_block = format_postmortems_for_prompt(str(conv["id"]))

    parts: list[str] = []
    if persona:
        parts.append(persona)
    parts.append(system)
    if skills_block:
        parts.append(skills_block)
    if rollback_block:
        parts.append(rollback_block)
    if sub_summary_block:
        parts.append(sub_summary_block)
    if sub_completion_block:
        parts.append(sub_completion_block)
    if postmortem_block:
        parts.append(postmortem_block)
    return "\n\n---\n\n".join(parts)


# ── Agent 工厂 ────────────────────────────────────────────────────────────────


def create_agent(model: str = "deepseek-v4-flash", conv: dict | None = None) -> Agent:
    """创建自建 Agent 实例，按 model 名自动路由 LLM 客户端。

    - ``mimo-*`` 系列 → MiMoClient（小米 MiMo，OpenAI 兼容，支持多模态）
    - 其他       → DeepSeekClient（DeepSeek 默认）
    两者协议一致，仅 base_url + api_key 不同。

    - reasoning_content 处理已内建到 ``Message.to_openai()``
    - tools 动态绑定（每次 astream 从 _REGISTRY 拉取）
    - 注入 audit logger 写 ``.sandbox/_meta/<tid>/audit.jsonl``
    - ``conv`` 传 master conv 时，prompt 注入已批准的子摘要

    server.py 现在每次 chat 都新建 Agent（共享 LLM client，重的是 prompt
    动态生成 —— 不重）；不再缓存 Agent 实例本身。
    """
    from audit import log_tool_event

    if model in MIMO_MODELS:
        llm = MiMoClient(model=model, temperature=0.7)
    else:
        llm = DeepSeekClient(model=model, temperature=0.7)
    return Agent(
        llm=llm,
        system_prompt=_full_prompt(conv=conv),
        on_tool_event=log_tool_event,
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
