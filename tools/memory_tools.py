"""长期记忆工具（5 个 @tool + 撤销 3 个）：

- ``remember``      存一条事实
- ``recall``        语义检索
- ``update_memory`` 改某条
- ``merge_memories`` 合并多条
- ``forget_memory`` 删某条
- ``restore_memory`` 从 trash 找回（不门控）
- ``list_trash``    看回收站（不门控）

**全部不再受 memory_write_enabled 开关控制** —— 所有破坏性操作前都会
自动 trash 快照 7 天可恢复（memory.py 内置），保护已足够；之前的开关
反而成了 friction，主人吐槽过"我不会去细看弹窗"。
"""
from __future__ import annotations

from ai_agent import tool


@tool
def remember(fact: str, category: str = "other", importance: int = 5) -> str:
    """把一条关于用户的事实存入长期记忆（跨对话生效）。

    什么时候用：
    - 用户明确说"以后记住..."、"我喜欢/讨厌..."、"我的 XX 是..."
    - 对话中涌现出值得跨对话复用的偏好、习惯、个人信息
    - 用户纠正你 / 表达不满，你应该把"纠正的方向"记下来

    什么时候**不**用：
    - 临时任务（"帮我处理这个文件"）—— 这是单次需求
    - 闲聊、时事 —— 没有跨对话价值
    - 已经存在的事实 —— 避免重复

    参数：
        fact: 一句话事实，应包含主语和具体内容。
              好例：「用户偏好简短、不啰嗦的回答」
              坏例：「简短」（没主语，检索时模糊）
        category: 记忆分类，三选一：
              - user_profile  用户画像（偏好/习惯/个人信息）
              - agent_directive  对有希的行为指示（"以后用...风格回答"）
              - other  其他（默认）
        importance: 权重 1-10，越大越重要：
              - 9-10  核心人设 / 强行为指令
              - 6-8   重要偏好 / 长期习惯
              - 3-5   普通信息（默认 5）
              - 1-2   临时信息

    返回：成功提示 + 记忆 ID 前 8 位。
    """
    from memory import add_memory
    try:
        mem_id = add_memory(fact, category=category, importance=importance)
        return f"已记入长期记忆 [{mem_id[:8]}] ({category}/重要度{importance})：{fact}"
    except Exception as e:
        return f"记忆存入失败：{e}"


@tool
def recall(query: str) -> str:
    """从长期记忆中检索与 query 相关的事实。

    什么时候用：
    - 新对话开始时（消息很少），先 recall 一次"用户偏好"拿到基本上下文
    - 用户问到 / 谈到个人信息、偏好、习惯时
    - 不确定用户期望的回答风格时

    参数：
        query: 检索关键词或自然语言描述。越具体越好。
              例：「用户对回答风格的偏好」、「用户的工作目录习惯」

    返回：最多 5 条相关记忆（按相关度排序，含时间）；未找到时返回明确提示。
    """
    from memory import search_memory
    try:
        results = search_memory(query, top_k=5)
    except Exception as e:
        return f"检索失败：{e}"
    if not results:
        return "（长期记忆库为空 / 未找到相关条目）"
    lines = []
    for r in results:
        cat = r.get("category", "other")
        imp = r.get("importance", 5)
        mid8 = r["id"][:8]
        lines.append(f"- [id:{mid8}] ({cat}/重要度{imp}) {r['text']}")
    lines.append("（若需 update/merge/delete，把 [id:xxxx] 里的 8 位前缀传给对应工具）")
    return "\n".join(lines)


async def _check_restricted_or_pass(action_summary: str, config: dict) -> str | None:
    """子对话受限模式权限网关：弹窗请求主人批准。

    返回 None = 放行（继续走原逻辑）
    返回错误字符串 = 拒绝（直接当工具结果返回给 Agent）
    """
    from tools.dialog import is_restricted_sub, require_master_approval
    if not is_restricted_sub(config):
        return None
    approved, raw = await require_master_approval(action_summary, config)
    if not approved:
        return (
            f"❌ 主人拒绝（或超时）了这次记忆编辑请求：{raw[:60]}。"
            f"请换思路或在主对话 / 高级模式子对话里操作。"
        )
    return None


@tool
async def update_memory(
    mem_id_prefix: str,
    text: str = "",
    category: str = "",
    importance: int = 0,
    config: dict = None,
) -> str:
    """更新一条已有记忆的文本 / 分类 / 权重。改前自动快照到 trash，7 天内可 restore。

    什么时候用：
    - 用户纠正了之前记错的事实（先 recall 找到旧条，再 update）
    - 你发现某条偏好的"权重"应该升高（用户反复强调时）

    **受限模式子对话**调本工具会先弹窗请求主人批准，主人不在 / 拒绝 → 工具返回拒绝消息。

    参数：
        mem_id_prefix: recall 输出里的 [id:xxxxxxxx] 8 位前缀。
        text:         新文本，传空字符串表示不改。
        category:     user_profile / agent_directive / other，传空字符串表示不改。
        importance:   1-10，传 0 表示不改。

    返回：成功提示 + 新内容；定位失败 / 主人拒绝时返回明确原因。
    """
    denied = await _check_restricted_or_pass(
        f"update_memory(prefix={mem_id_prefix!r}, text={text[:40]!r}, "
        f"category={category!r}, importance={importance})",
        config,
    )
    if denied:
        return denied

    from memory import find_memory_by_prefix
    from memory import update_memory as _update_memory_impl
    try:
        target = find_memory_by_prefix(mem_id_prefix)
    except (KeyError, ValueError) as e:
        return f"未能定位记忆：{e}"
    try:
        updated = _update_memory_impl(
            target["id"],
            text=text if text else None,
            category=category if category else None,
            importance=importance if importance else None,
        )
    except Exception as e:
        return f"更新失败：{e}"
    return (
        f"已更新 [{updated['id'][:8]}] "
        f"({updated['category']}/重要度{updated['importance']})：{updated['text']}"
    )


@tool
async def merge_memories(
    mem_id_prefixes: list[str],
    new_fact: str,
    category: str = "other",
    importance: int = 5,
    config: dict = None,
) -> str:
    """把若干条相近 / 重复的记忆合并为一条新记忆。受限子对话需主人批准。"""
    denied = await _check_restricted_or_pass(
        f"merge_memories({len(mem_id_prefixes or [])} 条 → new_fact={new_fact[:40]!r})",
        config,
    )
    if denied:
        return denied

    from memory import find_memory_by_prefix
    from memory import merge_memories as _merge_impl
    if not mem_id_prefixes:
        return "未提供任何 id 前缀"
    full_ids = []
    for p in mem_id_prefixes:
        try:
            full_ids.append(find_memory_by_prefix(p)["id"])
        except (KeyError, ValueError) as e:
            return f"前缀 {p!r} 解析失败：{e}"
    try:
        res = _merge_impl(full_ids, new_fact, category=category, importance=importance)
    except Exception as e:
        return f"合并失败：{e}"
    return (
        f"已合并 {res['deleted_count']} 条旧记忆 → 新记忆 [{res['new_id'][:8]}] "
        f"({res['category']}/重要度{res['importance']})：{res['new_fact']}"
    )


@tool
async def forget_memory(mem_id_prefix: str, config: dict = None) -> str:
    """删除一条记忆。删前自动快照到 trash，7 天内可 restore。受限子对话需主人批准。"""
    denied = await _check_restricted_or_pass(
        f"forget_memory(prefix={mem_id_prefix!r})", config,
    )
    if denied:
        return denied

    from memory import find_memory_by_prefix, delete_memory
    try:
        target = find_memory_by_prefix(mem_id_prefix)
    except (KeyError, ValueError) as e:
        return f"未能定位记忆：{e}"
    try:
        delete_memory(target["id"])
    except Exception as e:
        return f"删除失败：{e}"
    return f"已删除 [{target['id'][:8]}] ({target['category']})：{target['text']}"


# ── 撤销工具（C4）：从 trash 找回 ─────────────────────────────────────────────
# 这些工具**不受**"记忆编辑权限"门控 —— 撤销不算破坏，永远应该可用。
# 否则"开权限删 → 关权限 → 想撤销" 这种场景用户会被锁住。


@tool
async def restore_memory(mem_id_prefix: str, config: dict = None) -> str:
    """从 trash（回收站）恢复一条之前被 forget / merge / update 改掉的记忆。

    什么时候用：
    - 主人说"刚那条恢复一下"、"你不该删它"、"那条改错了"
    - 任何"撤销刚才的记忆操作"意图

    参数：
        mem_id_prefix: 被删 / 被改记忆的 8 位 id 前缀。如果不确定可先调
                       list_trash("memory") 看看 trash 里有什么。

    返回：恢复结果摘要（含原始 id / 新 id / 被快照时的动作类型）。
    **受限模式子对话**需主人批准（restore 也算改全局状态）。
    """
    denied = await _check_restricted_or_pass(
        f"restore_memory(prefix={mem_id_prefix!r})", config,
    )
    if denied:
        return denied

    from memory import restore_from_trash
    try:
        res = restore_from_trash(mem_id_prefix)
    except (KeyError, ValueError) as e:
        return f"恢复失败：{e}"
    return (
        f"已恢复 [{res['restored_from'][:8]}] → 新 id [{res['new_id'][:8]}] "
        f"({res['category']}/重要度{res['importance']})：{res['text']}"
        f"\n（原因：{res['trashed_action']}，快照时间：{res['trashed_at']}）"
    )


@tool
def list_trash(kind: str = "all") -> str:
    """列出回收站里 7 天内被删 / 修改的记忆和技能。

    什么时候用：
    - 主人说"看看你最近删了啥"、"trash 里有什么"
    - 你想给主人列候选让他选恢复哪条

    参数：
        kind: "memory" 只列记忆；"skills" 只列技能；"all"（默认）两边都列。

    返回：分类摘要，最多 30 条 / 类。
    """
    kind = (kind or "all").strip().lower()
    if kind not in ("all", "memory", "skills"):
        kind = "all"

    parts: list[str] = []

    if kind in ("all", "memory"):
        from memory import list_trash_items
        items = list_trash_items(limit=30)
        if not items:
            parts.append("【记忆回收站】（空）")
        else:
            lines = ["【记忆回收站】"]
            for it in items:
                lines.append(
                    f"- [id:{it['id'][:8]}] ({it.get('trashed_action','?')} @ "
                    f"{it.get('trashed_at','')}) {it.get('text','')[:60]}"
                )
            parts.append("\n".join(lines))

    if kind in ("all", "skills"):
        from tools.skills import list_skills_trash
        skl = list_skills_trash(limit=30)
        if not skl:
            parts.append("【技能回收站】（空）")
        else:
            lines = ["【技能回收站】"]
            for s in skl:
                lines.append(f"- {s['name']}（删除于 {s['trashed_at']}）")
            parts.append("\n".join(lines))

    return "\n\n".join(parts)
