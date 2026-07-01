"""自建 Agent 框架（无 LangChain / LangGraph 依赖）。

核心组件：
- messages.py  —— Message / ToolCall / Role
- tools.py     —— @tool 装饰器 + 注册表
- llm.py       —— DeepSeek / MiMo 流式客户端
- loop.py      —— ReAct loop（max 60 轮，断点续传）
- persist.py   —— JSONCheckpoint
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
