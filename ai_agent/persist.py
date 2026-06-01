"""对话持久化（取代 langgraph.checkpoint.sqlite.AsyncSqliteSaver）。

设计：每个 thread_id 一个独立 JSON 文件，存任意 JSON-serializable dict。
文件布局: ``<root>/<thread_id>/<filename>``（默认 conv.json）。

为什么是文件而不是 SQLite：
- 看得见摸得着（直接 cat / 编辑）
- 单文件独立，删除一个对话不影响其他
- 不需要 async driver（普通同步 IO 在 ASGI 里足够快，JSON 写入毫秒级）
- 调试方便：出错时直接打开文件看

Message 序列化由 ``message_to_dict`` / ``message_from_dict`` 辅助，
兼容旧格式（缺 tool_calls 等字段时默认空）。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .messages import Message, ToolCall


# ── Message 序列化辅助 ──────────────────────────────────────────────────────

def message_to_dict(m: Message, files: list[str] | None = None) -> dict[str, Any]:
    """Message → JSON-able dict。

    Args:
        m: 要序列化的消息
        files: 可选的 UI 元数据（生成的文件相对路径列表），通常附加在最后
            一条 assistant 消息上以便前端展示
    """
    d = asdict(m)
    if files is not None:
        d["files"] = files
    return d


def message_from_dict(d: dict[str, Any]) -> Message:
    """dict → Message，**兼容旧格式**。

    旧版 conv.json 只存 ``{role, content, files?}``，缺失的 tool_calls /
    tool_call_id / reasoning_content 默认空，反序列化不会报错。
    """
    tool_calls = [
        ToolCall(
            id=tc.get("id", ""),
            name=tc.get("name", ""),
            arguments=tc.get("arguments") or {},
        )
        for tc in (d.get("tool_calls") or [])
    ]
    return Message(
        role=d["role"],
        content=d.get("content") or "",
        tool_calls=tool_calls,
        tool_call_id=d.get("tool_call_id"),
        reasoning_content=d.get("reasoning_content"),
    )


# ── JSONCheckpoint 持久化器 ────────────────────────────────────────────────

class JSONCheckpoint:
    """每个 thread_id 一个独立 JSON 文件的对话持久化器。

    用法::

        ckpt = JSONCheckpoint(Path(".sandbox/_meta"))
        ckpt.save("tid-1", {"id": "tid-1", "messages": [...], ...})
        data = ckpt.load("tid-1")
        all_threads = ckpt.load_all()         # {tid: data}
        ckpt.delete("tid-1")
    """

    def __init__(self, root: Path | str, filename: str = "conv.json"):
        self.root = Path(root)
        self.filename = filename
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, thread_id: str) -> Path:
        return self.root / thread_id / self.filename

    # ── 单个对话 CRUD ─────────────────────────────────────────────────────

    def save(self, thread_id: str, data: dict[str, Any]) -> None:
        """原子写入（先写 .tmp 再 rename，崩溃时不会写到一半坏文件）。"""
        path = self._path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def load(self, thread_id: str) -> dict[str, Any] | None:
        """加载单个 thread；不存在或损坏时返回 None。"""
        path = self._path(thread_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def delete(self, thread_id: str) -> None:
        """删除单个 thread 的持久化文件。安静失败（文件不存在时无操作）。"""
        path = self._path(thread_id)
        if path.exists():
            path.unlink()
        # 尝试清理空目录
        parent = path.parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass

    # ── 批量加载 ──────────────────────────────────────────────────────────

    def load_all(self) -> dict[str, dict[str, Any]]:
        """加载所有 thread，返回 ``{thread_id: data}``。

        按文件修改时间排序（最早的在前，最新的在后），调用方可视需要重排。
        损坏的文件会打印警告并跳过。
        """
        out: dict[str, dict[str, Any]] = {}
        if not self.root.exists():
            return out
        dirs = [d for d in self.root.iterdir() if d.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime)
        for d in dirs:
            path = d / self.filename
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tid = data.get("id") or d.name
                out[tid] = data
            except (json.JSONDecodeError, OSError) as e:
                print(f"[JSONCheckpoint] failed to load {path}: {e}")
        return out

    def list_threads(self) -> list[str]:
        """列出所有存在持久化文件的 thread_id。"""
        if not self.root.exists():
            return []
        return [
            d.name
            for d in self.root.iterdir()
            if d.is_dir() and (d / self.filename).exists()
        ]
