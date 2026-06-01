"""统一的消息对象，将取代 langchain_core.messages 中的 HumanMessage / AIMessage。

Phase 1 仅建立类，业务代码暂未启用；Phase 2 起在 LLM 客户端中真正使用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    """LLM 决定要调用的一次工具。

    Attributes:
        id: 工具调用 ID（与 OpenAI/DeepSeek API 协议一致，便于把 tool 结果
            消息通过 tool_call_id 关联回来）。
        name: 工具名（注册表中的 key）。
        arguments: 已解析的 JSON 参数对象。
    """
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """统一的对话消息，可双向序列化为 OpenAI 兼容 API 的消息格式。

    设计要点：
    - 与 OpenAI/DeepSeek API 协议直接对应，不做无谓抽象。
    - **`reasoning_content` 是一等字段**：DeepSeek thinking 模式下，下一轮请求
      必须把上轮的 reasoning_content 一并传回，否则 API 报 400。这一点是
      langchain-deepseek 上游 bug 的修补点，我们这里直接内建处理。
    """

    role: Role
    # content 接受两种形态（OpenAI 多模态标准）：
    # - str：纯文本（绝大多数情况，向后兼容旧 conv.json）
    # - list[dict]：多模态，形如
    #   [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:..."}}]
    content: Any = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None       # 仅 role="tool" 时有
    reasoning_content: str | None = None  # DeepSeek thinking mode 字段

    def to_openai(self) -> dict[str, Any]:
        """转成 OpenAI 兼容 API 请求体的单条消息。"""
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.reasoning_content:
            d["reasoning_content"] = self.reasoning_content
        return d

    @classmethod
    def from_openai_response(cls, choice: dict[str, Any]) -> "Message":
        """从 OpenAI 响应的单个 choice 反序列化。

        choice 形如：
            {"message": {"role": "assistant", "content": "...",
                          "tool_calls": [...], "reasoning_content": "..."}}
        """
        msg = choice.get("message", {}) or {}
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=args,
            ))
        return cls(
            role=msg.get("role", "assistant"),
            content=msg.get("content") or "",
            tool_calls=tool_calls,
            tool_call_id=msg.get("tool_call_id"),
            reasoning_content=msg.get("reasoning_content"),
        )

    # ── 便利构造器 ──────────────────────────────────────────────────────────

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str = "", tool_calls: list[ToolCall] | None = None,
                  reasoning_content: str | None = None) -> "Message":
        return cls(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
            reasoning_content=reasoning_content,
        )

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id)
