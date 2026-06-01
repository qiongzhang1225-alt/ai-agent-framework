"""spawn_sub_conversation 工具：让有希在主对话里自己开子对话。

仅在 master 对话里可用（其他对话调用拒绝）。创建后返回特殊跳转标记
``[→sub:<id>|<name>]``，前端识别后渲染成可点击 chip。
"""
from __future__ import annotations

from ai_agent import tool


@tool
def spawn_sub_conversation(
    name: str,
    sub_level: str = "restricted",
    config: dict = None,
) -> str:
    """在**主对话**里创建一个新的子对话挂到自己（"有希"）下面。

    什么时候用：
    - 主人在主对话里说"开个对话做 X" / "新开一个聊 Y" / "专门讨论 Z"
    - 你提议"我开个子对话专门做 X 吧？" 主人同意后
    - 任务**足够独立 + 预期对话量 ≥ 5 轮**，适合专用窗口

    什么时候**不要**用：
    - 你不在 master 对话里（工具会拒绝；告诉主人在 sidebar 点"新对话"）
    - 任务太小（1-2 轮就完事）—— 直接在当前对话回
    - 主人只是闲聊里随口提了一句，没明确要"专门开"

    参数：
        name: 子对话名（**短而准确**，如 "UI 设计讨论" / "销售报告 v3"）
        sub_level: 权限级别
            - ``"restricted"``（默认，安全）：日常任务；破坏性操作需主人批准
            - ``"advanced"``：自我优化、复杂技术、长流程等；权限 ≈ 主对话
        如果不确定选哪个，**默认 restricted**。主人后续可在 header 上一键切换。

    返回：成功提示，含 ``[→sub:<id>|<name>]`` 跳转标记 ——
    前端会自动渲染成可点击 chip，主人点击就跳过去。
    告诉主人后让他自己决定何时开始（或者立即跳过去聊）。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}

    # 只允许在 master 对话里调
    conv_kind = cfg.get("conv_kind")
    if conv_kind != "master":
        return (
            f"❌ 当前对话不是主对话（kind={conv_kind!r}），无法 spawn 子对话。"
            f"spawn_sub_conversation 只在主对话\"有希\"里可用。"
            f"告诉主人：在 sidebar 顶部点\"新对话\"按钮选 sub_advanced / sub_restricted 即可。"
        )

    if sub_level not in ("restricted", "advanced"):
        return f"❌ sub_level 必须是 'restricted' 或 'advanced'（你传了 {sub_level!r}）"

    name = (name or "").strip()
    if not name:
        return "❌ name 不能为空 —— 给子对话一个简短描述性的名字（如 'UI 设计讨论'）"
    if len(name) > 40:
        return f"❌ name 太长（{len(name)} 字符），请用 ≤ 40 字"

    try:
        import server
        master_id = cfg.get("thread_id") or server.MASTER_CONV_ID
        master_conv = server.conversations.get(master_id)
        if not master_conv or master_conv.get("kind") != "master":
            return f"❌ 当前 thread_id {master_id!r} 不是有效的 master 对话"

        new_sub = server.new_conversation(
            model_id=master_conv.get("model", "deepseek-v4-flash"),
            kind="sub",
            parent_id=master_id,
            sub_level=sub_level,
            name=name,
        )
    except Exception as e:
        return f"❌ 创建失败：{type(e).__name__}: {e}"

    level_label = "🛡️ 高级模式" if sub_level == "advanced" else "🔒 受限模式"
    # 用标准 markdown 链接格式 [text](#sub=id)，前端识别 href 前缀 "#sub=" 渲染为
    # 跳转 chip。比自创语法稳：LLM 不会"美化"标准 markdown link。
    return (
        f"✓ 已创建子对话「{name}」（{level_label}）。\n\n"
        f"[→ {name}](#sub={new_sub['id']})\n\n"
        f"主人可以点上面的链接立即跳转过去开聊，或者稍后在 sidebar 找到它。"
        f"\n\n**重要**：你回复主人时**必须原样保留** `[→ {name}](#sub={new_sub['id']})` "
        f"这条 markdown 链接（不要改成别的格式 / 不要简化），"
        f"否则前端识别不到，主人没法跳转。"
    )
