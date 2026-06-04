"""工具装饰器与注册表（Phase 5 后：彻底脱离 LangChain）。

`@tool` 装饰器：
- 解析 Python 函数签名 + docstring + 类型注解
- 生成 OpenAI tool-calling 协议要求的 JSON Schema
- 注册到本地 `_REGISTRY`（供 `ai_agent.Agent` 调度）
- **直接返回原函数**，不再包装

之前 Phase 1-4 期间，这里桥接了 ``langchain_core.tools.tool`` 以兼容
``langgraph.create_react_agent`` —— 现在已彻底拆除。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, get_origin, get_type_hints


@dataclass
class Tool:
    """工具的完整元数据。

    `parameters` 是 OpenAI tool-calling 协议要求的 JSON Schema：
        {"type": "object", "properties": {...}, "required": [...]}
    """
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable
    needs_config: bool = False  # 函数签名是否含 `config` 参数（由框架注入）


_REGISTRY: dict[str, Tool] = {}

# Python 类型 → JSON Schema 类型
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _build_tool_meta(func: Callable) -> Tool:
    """从 Python 函数的签名 + 类型注解 + docstring 抽取 Tool 元数据。"""
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        # 如果有未导入的类型注解，宽容处理
        hints = {}

    docstring = inspect.getdoc(func) or func.__name__

    props: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    needs_config = False

    for pname, param in sig.parameters.items():
        # config 是框架注入参数，不暴露给 LLM
        if pname == "config":
            needs_config = True
            continue
        py_type = hints.get(pname, str)
        json_type = _TYPE_MAP.get(get_origin(py_type) or py_type, "string")
        props[pname] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return Tool(
        name=func.__name__,
        description=docstring,
        parameters={
            "type": "object",
            "properties": props,
            "required": required,
        },
        func=func,
        needs_config=needs_config,
    )


def tool(func: Callable) -> Callable:
    """把 Python 函数注册为 agent 可调用的工具。

    用法::

        @tool
        def my_tool(arg: str) -> str:
            \"\"\"工具的描述（会作为 description 喂给 LLM）。\"\"\"
            ...

    会解析签名 + docstring，生成 OpenAI 协议的 JSON Schema 并注册到全局
    `_REGISTRY`。装饰后**直接返回原函数**，可以照常 Python 调用。
    """
    meta = _build_tool_meta(func)
    _REGISTRY[meta.name] = meta
    return func


def list_tools() -> list[Tool]:
    """返回所有已注册工具的元数据列表（按注册顺序）。"""
    return list(_REGISTRY.values())


def get_tool(name: str) -> Tool | None:
    """按名字查工具元数据；不存在返回 None。"""
    return _REGISTRY.get(name)


def unregister(name: str) -> bool:
    """从注册表移除一个工具（用于动态技能撤销）。返回是否真的删除了。"""
    return _REGISTRY.pop(name, None) is not None


def register(meta: Tool) -> None:
    """直接注册一个 Tool 元数据（用于动态技能加载）。"""
    _REGISTRY[meta.name] = meta


def build_tool_meta(func: Callable) -> Tool:
    """对外暴露的 Tool 元数据构造器（动态技能加载时用）。"""
    return _build_tool_meta(func)


def clear_registry() -> None:
    """清空注册表（仅供测试用）。"""
    _REGISTRY.clear()
