"""complete_sub_conversation 工具 + 主对话注入逻辑。

yuki 在 sub conv 完成主要任务后**主动调一次**，给主对话留个结构化交代。

设计动机:
原本 spawn_sub_conversation 启动子对话后，sub 里改了什么 / 学到什么，
切回主对话时 yuki 一无所知。已有的 _format_approved_sub_summaries 需要
主人手动"批准纳入主"才生效，半自动；postmortem 只记教训不记成果。

complete_sub_conversation 填补这个缺口：sub conv 自己产出"完成摘要"，
含改动文件 / commit / 遗留决策 / 一句话总结，存到子对话 conv.json 的
``completion_summary`` 字段 + remember 写入长期记忆。

主对话下次 _full_prompt 自动 inject 最近 5 条 completion_summary。
"""
from __future__ import annotations

import json
from datetime import datetime

from ai_agent import tool
from paths import META_DIR


def _check_is_sub(config: dict) -> str | None:
    """非 sub conv 调本工具 → 拒绝（返回错误消息），否则 None。"""
    cfg = (config or {}).get("configurable", {}) if config else {}
    if cfg.get("conv_kind") != "sub":
        return (
            "❌ complete_sub_conversation 只能在 sub conv 调。"
            "当前 conv_kind = " + str(cfg.get("conv_kind"))
        )
    return None


@tool
def complete_sub_conversation(
    one_line_summary: str,
    changed_files: list = None,
    commits: list = None,
    leftover: str = "",
    config: dict = None,
) -> str:
    """子对话主要任务完成后**主动调一次**，给主对话留下结构化交代。

    什么时候调:
    - 子对话主任务做完（修完 bug / 重构完 / 文档写完 等）
    - 主人切回主对话之前
    - 不论 outcome 是 done / partial / abandoned，都该调

    与 write_postmortem 的区别:
    - write_postmortem 关注"教训"（lesson punchline，跨任务复用）
    - complete_sub_conversation 关注"成果"（改了什么 / commit hash / 遗留）
    两个工具互不替代，复杂任务两个都调。

    机制:
    - 写到子对话 conv.json 的 completion_summary 字段
    - remember(category="sub_completion", importance=7) 跨对话生效
    - 主对话下次启动时，最近 5 条 completion_summary 自动 inject 到
      system prompt（让有希切回主对话立刻知道 sub 干了啥）

    Args:
        one_line_summary: 一句话总结（"修了 launcher.py 的 splash 时序 bug"）
        changed_files: 改动文件列表（相对项目根）
        commits: commit hash 列表（短 hash 即可，从 self_diff 拿）
        leftover: 遗留决策 / 未完事项（可空，但有就写清楚让主对话能跟进）
        config: (注入参数，不用填)

    Returns:
        确认 + 主对话能看到的提示。

    只能在 **sub conv** 调；主对话 / standalone 调会被拒。
    """
    err = _check_is_sub(config)
    if err:
        return err

    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "")
    if not thread_id:
        return "❌ 缺 thread_id（系统配置问题，告诉主人）"

    one_line_summary = (one_line_summary or "").strip()
    if not one_line_summary:
        return "❌ one_line_summary 不能为空"

    changed_files = [str(f).strip() for f in (changed_files or []) if str(f).strip()]
    commits = [str(c).strip() for c in (commits or []) if str(c).strip()]
    leftover = (leftover or "").strip()

    # 写入 conv.json
    conv_path = META_DIR / thread_id / "conv.json"
    if not conv_path.exists():
        return f"❌ 找不到 conv.json: {conv_path}"

    try:
        data = json.loads(conv_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"❌ 读 conv.json 失败: {e}"

    completion = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "one_line_summary": one_line_summary,
        "changed_files": changed_files,
        "commits": commits,
        "leftover": leftover,
        "conv_name": data.get("name", ""),
        "parent_id": data.get("parent_id", ""),
    }
    data["completion_summary"] = completion

    # atomic write
    tmp = conv_path.with_name(conv_path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(conv_path)

    # 同步到长期记忆（让主对话能看到）
    mem_ok = False
    try:
        from memory import add_memory
        mem_text = f"[子对话「{data.get('name','?')}」完成] {one_line_summary}"
        if changed_files:
            mem_text += f"\n  改动: {', '.join(changed_files[:6])}"
            if len(changed_files) > 6:
                mem_text += f" 等 {len(changed_files)} 个"
        if commits:
            mem_text += f"\n  commit: {', '.join(commits[:4])}"
        if leftover:
            mem_text += f"\n  遗留: {leftover[:200]}"
        add_memory(mem_text, category="sub_completion", importance=7)
        mem_ok = True
    except Exception as e:
        print(f"[sub_complete] remember 失败（不阻塞）: {e}")

    out = [
        f"✅ 子对话「{data.get('name','?')}」完成摘要已记录",
        f"一句话: {one_line_summary}",
    ]
    if changed_files:
        out.append(f"改动文件: {len(changed_files)} 个")
    if commits:
        out.append(f"Commits: {', '.join(commits[:4])}")
    if leftover:
        out.append(f"遗留: {leftover[:100]}")
    out.append("")
    out.append("主对话下次启动时会自动看到这条摘要" + (" + 长期记忆" if mem_ok else "（记忆库写入失败，仅本地）"))
    return "\n".join(out)


# ── 给主对话的 prompt 注入器 ──────────────────────────────────────────


def format_recent_sub_completions_for_prompt(limit: int = 5) -> str:
    """读最近 N 条 sub conv 的 completion_summary，拼成主对话 system prompt 段。

    跟 _format_approved_sub_summaries 的区别:
    - 那个要主人手动"批准纳入主"
    - 这个 yuki 自己 complete 就生效（更主动）

    空时返回空字符串（agent.py 跳过该段）。
    """
    if not META_DIR.exists():
        return ""

    items = []  # (ts, name, summary_dict)
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
        comp = data.get("completion_summary")
        if not comp or not isinstance(comp, dict):
            continue
        items.append((comp.get("ts", ""), data.get("name", "?"), comp))

    if not items:
        return ""

    items.sort(reverse=True)
    lines = [
        "【主对话上下文 - 子对话完成摘要（最近）】",
        "你在子对话里完成的任务，会自动汇总到这里。聊到相关话题可引用。",
        "",
    ]
    for ts, name, comp in items[:limit]:
        lines.append(f"### 「{name}」（{ts[:16].replace('T', ' ')}）")
        lines.append(f"- {comp.get('one_line_summary', '?')}")
        if comp.get("changed_files"):
            lines.append(f"- 改动: {', '.join(comp['changed_files'][:5])}")
        if comp.get("commits"):
            lines.append(f"- Commits: {', '.join(comp['commits'][:4])}")
        if comp.get("leftover"):
            lines.append(f"- 遗留: {comp['leftover'][:200]}")
        lines.append("")
    return "\n".join(lines).rstrip()
