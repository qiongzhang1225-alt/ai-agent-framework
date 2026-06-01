"""自建 Agent 框架（替换 LangChain / LangGraph）。

迁移分 5 个 Phase，详见 MIGRATION.md。
当前阶段：**Phase 1** —— 消息类型 + 工具装饰器（桥接模式，
仍委托 LangChain 进入 LangGraph 主循环；Phase 3 完成后将彻底脱离）。
"""

from .messages import Message, ToolCall, Role
from .tools import (
    tool, Tool, list_tools, get_tool,
    register, unregister, build_tool_meta,
)
from .llm import DeepSeekClient, MiMoClient
from .loop import Agent
from .persist import JSONCheckpoint, message_to_dict, message_from_dict

__all__ = [
    "Message", "ToolCall", "Role",
    "tool", "Tool", "list_tools", "get_tool",
    "register", "unregister", "build_tool_meta",
    "DeepSeekClient", "MiMoClient",
    "Agent",
    "JSONCheckpoint", "message_to_dict", "message_from_dict",
]
