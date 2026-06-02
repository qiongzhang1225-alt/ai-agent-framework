"""write_postmortem 工具 + 读取逻辑 —— 任务复盘沉淀。

设计动机：
assistant 在一个任务里翻车后，下次同类任务还会翻同样的车（白天主题做了 2 轮
都没用 CSS Variables 路径）。原因是 LLM 没有跨 task 的隐式记忆。

机制：
- 任务完成后她调 write_postmortem 把教训写下来
- 存到 .sandbox/_meta/<tid>/postmortems/<ts>.md
- 下次同 thread 启动时，agent.py 的 _full_prompt 会自动 inject 最近 3 个
  postmortem 到 system prompt 末尾

per-thread 而非 global 的原因：
- 不同 thread 任务性质不同（主对话是聊天 / sub 是单一任务）
- 全局复盘列表会很快膨胀到 inject 不下
- 同 thread 的 postmortem 通常跟当前任务相关性最高
"""
from datetime import datetime
from pathlib import Path

from ai_agent import tool
from paths import META_DIR


def _postmortems_dir(thread_id: str) -> Path:
    d = META_DIR / thread_id / "postmortems"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_recent_postmortems(thread_id: str, n: int = 3) -> list[dict]:
    """读最近 N 个 postmortem（供 system prompt 注入用）。

    返回 [{"ts": ..., "task": ..., "lesson": ..., "raw": <md text>}]。
    """
    d = META_DIR / thread_id / "postmortems"
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for f in files[:n]:
        try:
            text = f.read_text(encoding="utf-8")
            # 简单解析头几个字段
            task = ""
            lesson = ""
            for line in text.splitlines():
                if line.startswith("## 任务"):
                    pass
                elif line.startswith("**任务**:"):
                    task = line[len("**任务**:"):].strip()
                elif line.startswith("**教训**:"):
                    lesson = line[len("**教训**:"):].strip()
            out.append({
                "ts": f.stem,
                "task": task,
                "lesson": lesson,
                "raw": text,
            })
        except Exception:
            pass
    return out


def format_postmortems_for_prompt(thread_id: str) -> str:
    """拼接最近 3 个 postmortem 成 system prompt 注入段。

    空时返回空字符串（agent.py 跳过该段）。
    """
    items = load_recent_postmortems(thread_id, n=3)
    if not items:
        return ""

    lines = [
        "【本对话历史教训 - 最近 3 次复盘】",
        "下面是你在这个对话里之前任务的总结。**遇到类似任务先看这里**，",
        "避免重复同样的错。",
        "",
    ]
    for i, p in enumerate(items, 1):
        lines.append(f"### {i}. {p['task'] or '(未填任务)'}（{p['ts']})")
        if p["lesson"]:
            lines.append(f"教训：{p['lesson']}")
        lines.append("")
    return "\n".join(lines).rstrip()


@tool
def write_postmortem(
    task: str,
    outcome: str,
    what_worked: str,
    what_failed: str,
    lesson: str,
    would_redo: str = "",
    config: dict = None,
) -> str:
    """**任务完成 / 放弃后调一次**：写一份复盘 —— 下次同对话会自动看到。

    什么时候必须调（硬约束）：
    - 完成一个**复杂任务**（之前调过 plan_task 的都算）
    - 任务**做砸了**主人喊停时 —— 这种最该写，避免重蹈覆辙

    什么时候不用调：
    - 简单 query 答完
    - 改个错别字

    机制：
    复盘存到 .sandbox/_meta/<thread_id>/postmortems/<ts>.md。
    下次这个 thread 任何新对话回合启动时，**最近 3 个 postmortem 自动
    inject 到你的 system prompt**。

    所以："任务做完了"是个**主动行为** —— 你写复盘 = 给未来的自己留信。
    不写 = 未来的你照样翻同样的车。

    Args:
        task: 一句话任务概述（"做白天主题"）
        outcome: 结果，三选一：``"done"`` / ``"partial"`` / ``"abandoned"``
        what_worked: 什么行得通。一两句话。**具体到方法**，比如
                     "用 CSS Variables 集中 token，一处改全跟随"
        what_failed: 什么没成。**具体到坑**，比如
                     "CSS 选择器写 `\\\\/` 双反斜杠，浏览器静默忽略，30 处死代码"
        lesson: 这次学到什么。**一句话 punchline**。
                "改 CSS 后必须 grep `\\\\\\\\/` 验证转义"
        would_redo: 如果重来怎么做。可空。
        config: (注入参数，不用填)

    Returns:
        确认 + 文件路径 + 提醒 inject 机制。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")

    # 字段校验
    task = (task or "").strip()
    if not task:
        return "❌ task 不能为空"
    outcome = (outcome or "").strip().lower()
    if outcome not in {"done", "partial", "abandoned"}:
        return "❌ outcome 必须是 'done' / 'partial' / 'abandoned' 之一"
    lesson = (lesson or "").strip()
    if len(lesson) < 8:
        return "❌ lesson 太短（< 8 字符）—— 至少写一句可操作的教训"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}.md"

    md = [
        f"# {ts}",
        "",
        f"**任务**: {task}",
        f"**结果**: {outcome}",
        f"**教训**: {lesson}",
        "",
        "## 行得通的",
        (what_worked or "").strip() or "(未填)",
        "",
        "## 翻车的",
        (what_failed or "").strip() or "(未填)",
    ]
    if would_redo.strip():
        md.extend(["", "## 重来怎么做", would_redo.strip()])

    path = _postmortems_dir(thread_id) / fname
    path.write_text("\n".join(md), encoding="utf-8")

    # 自动把 lesson 记入长期记忆（跨对话生效）
    _remember_lesson(task, lesson, thread_id)

    return (
        f"✅ postmortem 已写入：{path.name}\n"
        f"任务：{task}\n"
        f"教训：{lesson}\n"
        f"\n"
        f"下次这个对话启动时，最近 3 个 postmortem（含本条）会自动 inject 到你的 system prompt。\n"
        f"主人能在 .sandbox/_meta/{thread_id}/postmortems/ 看完整列表。"
    )


def _remember_lesson(task: str, lesson: str, thread_id: str) -> None:
    """把教训记入长期记忆，跨对话也可见。"""
    if not lesson or len(lesson) < 8:
        return
    try:
        from memory import add_memory, search_memory
        # 先查是否有高度相似的已有记忆
        existing = search_memory(lesson, top_k=3)
        for mem in existing:
            if mem and lesson in mem.get("text", ""):
                return  # 已有类似教训，不重复存
        add_memory(
            f"[复盘] {task} | 教训: {lesson}",
            category="agent_directive",
            importance=8,
        )
    except Exception:
        pass  # 记忆写入失败不影响主流程
