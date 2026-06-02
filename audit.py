"""工具调用审计日志。

把每次工具调用（before / after）写到
``.sandbox/_meta/<thread_id>/audit.jsonl``，每行一个 JSON 对象，便于：
- 用户事后查"私人助手什么时候改了什么"
- debug 工具调用链
- 万一出问题作为恢复线索

设计成"事后审计 + 可回滚"而非"事前弹窗"—— 用户偏好不被打断。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from paths import META_DIR


def _audit_path(thread_id: str) -> Path:
    d = META_DIR / thread_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "audit.jsonl"


def _truncate(s: str, n: int = 500) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"...(截断,原 {len(s)} 字符)"


def log_tool_event(event: dict[str, Any], config: dict[str, Any]) -> None:
    """落审计日志。事件结构由 ai_agent/loop.py 的 hook 传入。

    Args:
        event: 至少含 ``phase``（"before"/"after"）、``tool``、``args``；
               after 还会有 ``result_preview``、``duration_ms``、``ok``。
        config: agent 的 config 字典（含 thread_id 等）。

    任何异常都被吞掉（审计失败不该影响主流程）。
    """
    try:
        thread_id = str(config.get("thread_id") or "default")
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            **event,
        }
        # 把容易超长的字段截断
        if "args" in record:
            try:
                record["args"] = json.loads(json.dumps(record["args"], ensure_ascii=False, default=str))
            except Exception:
                record["args"] = str(record["args"])[:500]
        if "result_preview" in record:
            record["result_preview"] = _truncate(str(record["result_preview"]), 500)

        path = _audit_path(thread_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 审计日志失败永远不上抛 —— 不能因为日志写不进去就让工具调用挂掉
        pass


def read_audit(thread_id: str, limit: int = 200) -> list[dict]:
    """读最近 limit 条审计记录（按时间顺序，旧→新）。"""
    path = _audit_path(thread_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
