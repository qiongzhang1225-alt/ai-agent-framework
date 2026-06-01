"""对话级 Todo List 工具（D1）。

让有希在复杂多步任务里自己列清单 + 标记进度，前端浮卡实时显示。

设计要点：
- **per-thread** 持久化在 ``.sandbox/_meta/<tid>/todos.json``
  每个对话有自己的清单；切换对话或新开对话清单互不影响
- ``todo_write`` 完整替换当前清单（不是 patch）——
  调用方一次性传入完整 items 即可，简单可靠
- status 三态：``pending`` / ``in_progress`` / ``completed``
- 任何时候只应有 ≤ 1 个 ``in_progress``（prompt 引导，不强制校验）
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ai_agent import tool
from paths import META_DIR

VALID_STATUSES = ("pending", "in_progress", "completed")


def _todos_path(thread_id: str) -> Path:
    d = META_DIR / thread_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "todos.json"


def load_todos(thread_id: str) -> list[dict]:
    """读取某个对话的 todo 列表（空时返回 []）。"""
    path = _todos_path(thread_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return []
    except Exception:
        return []


def save_todos(thread_id: str, items: list[dict]) -> list[dict]:
    """覆盖写入 todos.json，返回规范化后的清单。"""
    cleaned: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"
        cleaned.append({"content": content, "status": status})

    payload = {
        "items": cleaned,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    path = _todos_path(thread_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


@tool
def todo_write(items: list[dict], config: dict) -> str:
    """写入 / 覆盖当前对话的 todo 清单。前端会立刻刷新浮卡。

    什么时候用：
    - 用户给你一个**多步**任务（≥ 3 步），写清单帮自己跟踪
    - 多个相对独立的子任务并列，列出来让用户也能看到进度
    - 已经完成某一步时**重新调一次** todo_write，把对应项的 status 改成 completed
    - 中途任务变化（删项 / 加项 / 改顺序）也重新调一次

    什么时候**不要**用：
    - 任务只有 1-2 步 —— 多余
    - 用户问"现在几点"这种一次性查询
    - 你自己内部的思考步骤（写 prompt 里就行，不要污染 UI）

    使用要点：
    - **items 是完整清单**，不是增量。每次调用都会替换之前的所有项。
    - 同一时刻**只应有 ≤ 1 个 ``in_progress``**（当前在做的那个）
    - 完成的留在清单里（状态改 completed），不要删 —— 用户能看到你做完了什么
    - 开始下一步前，**先把上一步的状态从 in_progress 改成 completed**

    参数：
        items: list of dict，每个 dict 含：
            - content: str  任务描述（必填）
            - status: "pending" / "in_progress" / "completed"（默认 pending）

    示例（写入 3 项，正在做第二步）：
        todo_write(items=[
            {"content": "读取 sales.xlsx", "status": "completed"},
            {"content": "按地区汇总销量", "status": "in_progress"},
            {"content": "生成柱状图", "status": "pending"},
        ])

    返回：摘要文本（多少项、状态分布）。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")

    cleaned = save_todos(thread_id, items)
    if not cleaned:
        return "已清空 todo 清单"

    counts = {"pending": 0, "in_progress": 0, "completed": 0}
    for it in cleaned:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return (
        f"已更新 todo 清单（共 {len(cleaned)} 项："
        f"完成 {counts['completed']} / 进行中 {counts['in_progress']} / 待办 {counts['pending']}）"
    )


@tool
def todo_read(config: dict) -> str:
    """读取当前对话的 todo 清单，看自己之前列了什么、做到哪一步。

    什么时候用：
    - 复杂任务做了一会后**回头看清单**确认进度
    - 用户问"你做到哪了" / "还剩什么"
    - 不确定下一步该做什么时

    返回：清单的人类可读摘要（空清单时返回明确提示）。
    """
    cfg = (config or {}).get("configurable", {}) if config else {}
    thread_id = str(cfg.get("thread_id") or "default")
    items = load_todos(thread_id)
    if not items:
        return "（当前对话没有 todo 清单）"
    lines = []
    icons = {"pending": "□", "in_progress": "▶", "completed": "✓"}
    for i, it in enumerate(items, 1):
        icon = icons.get(it["status"], "?")
        lines.append(f"{i}. {icon} {it['content']}")
    return "\n".join(lines)
