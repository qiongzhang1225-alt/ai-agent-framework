"""MCP（Model Context Protocol）客户端桥接。

让 yuki 能像调本地工具一样调外部 MCP server 的工具。**第一个目标场景：
连接 Unity MCP server，让 yuki 操作 Unity Editor**。

设计要点
========

1. **通用，不绑 Unity**：任何符合 MCP 标准的 server 都能挂上来
   （Unity / GitHub / Filesystem / 你自家的 server 都行）
2. **配置文件驱动**：``mcp.json`` 在 ``APP_DIR``（yuki.exe 旁 / 源码模式
   项目根）。结构跟 Claude Code 一致：

   .. code-block:: json

      {
        "mcpServers": {
          "unity": {
            "command": "uvx",
            "args": ["unity-mcp-server"],
            "enabled": false
          }
        }
      }

3. **生命周期跟着 yuki server 走**：
   - FastAPI startup 钩子里 ``init()``：spawn 所有 enabled 的 server
     子进程，握手，列工具，每个 MCP 工具动态注册成 yuki ``@tool``
   - FastAPI shutdown 钩子里 ``shutdown()``：关 session、kill 子进程
4. **工具命名前缀**：``mcp_<server>_<原名>`` 避免跟内置工具撞名。
   例：``mcp_unity_manage_gameobject``
5. **失败不阻塞**：某个 MCP server 起不来 / 工具调失败，记一行日志继续，
   不影响 yuki 主功能。

frozen 模式（yuki.exe）注意
============================
- MCP 子进程是**外部命令**（``uvx`` / ``npx`` / ``python -m xxx``）。
  必须在系统 PATH 里能找到，跟 yuki 打不打包无关。
- ``mcp`` Python SDK 已通过 ``yuki.spec`` 的 ``collect_all`` 打进 exe。
- ``subprocess.Popen`` 用绝对命令名（``shutil.which`` 解析），frozen 下
  跟源码一致。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from ai_agent.tools import Tool, register, _REGISTRY


# ── 模块级状态 ──────────────────────────────────────────────────────────
_exit_stack: AsyncExitStack | None = None
_sessions: dict[str, Any] = {}        # server_name -> ClientSession
_registered_tool_names: set[str] = set()
_init_lock = asyncio.Lock()
_initialized = False


# ── 配置加载 ────────────────────────────────────────────────────────────

def _resolve_app_dir() -> Path:
    """跟 paths.py 里 PROJECT_ROOT 同一套逻辑（frozen vs 源码）。"""
    from paths import PROJECT_ROOT
    return PROJECT_ROOT


def _config_path() -> Path:
    """MCP 配置文件路径优先级:

    1. 环境变量 ``YUKI_MCP_CONFIG``
    2. ``<APP_DIR>/mcp.json``
    """
    env_path = os.environ.get("YUKI_MCP_CONFIG", "").strip()
    if env_path:
        return Path(env_path)
    return _resolve_app_dir() / "mcp.json"


def _load_config() -> dict[str, Any]:
    """读 mcp.json；找不到 / 解析失败 → 返回空 dict（不报错）。"""
    p = _config_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[mcp] 解析 {p} 失败（视作空配置）: {e}", file=sys.stderr)
        return {}


# ── 工具包装器（把 MCP 工具变成 yuki 工具）─────────────────────────────

def _make_mcp_tool_wrapper(
    server_name: str,
    session: Any,
    mcp_tool: Any,
) -> Any:
    """生成一个 async yuki 工具函数，转发到对应 MCP server 的工具。"""
    mcp_tool_name = mcp_tool.name

    async def wrapper(**kwargs: Any) -> str:
        try:
            result = await session.call_tool(mcp_tool_name, kwargs)
        except Exception as e:
            return f"❌ MCP 调用失败 ({server_name}.{mcp_tool_name}): {type(e).__name__}: {e}"

        # 提取文本结果（MCP 结果可能是多段 content）
        parts: list[str] = []
        if getattr(result, "isError", False):
            parts.append("❌ MCP 工具返回错误:\n")
        for content in (result.content or []):
            ctype = getattr(content, "type", "")
            if ctype == "text":
                parts.append(getattr(content, "text", ""))
            elif ctype == "image":
                # 图片资源只附简短摘要，避免淹没 LLM 上下文
                mime = getattr(content, "mimeType", "?")
                parts.append(f"[image content: {mime}]")
            else:
                parts.append(f"[content type={ctype}]")

        return "".join(parts).strip() or "(MCP 工具返回空)"

    yuki_tool_name = f"mcp_{server_name}_{mcp_tool_name}"
    wrapper.__name__ = yuki_tool_name
    wrapper.__doc__ = (
        mcp_tool.description or f"MCP 工具 {server_name}.{mcp_tool_name}"
    )
    return wrapper, yuki_tool_name


def _register_mcp_tool(
    server_name: str,
    session: Any,
    mcp_tool: Any,
) -> str | None:
    """把单个 MCP 工具注册成 yuki 工具。返回 yuki 工具名（成功）或 None。"""
    func, yuki_name = _make_mcp_tool_wrapper(server_name, session, mcp_tool)

    # 跟 _build_tool_meta 等价，但 parameters 直接用 MCP 给的 inputSchema
    # MCP 的 inputSchema 已经是 JSON Schema 格式，跟 OpenAI tool calling
    # 协议兼容，不用转换。
    schema = getattr(mcp_tool, "inputSchema", None) or {
        "type": "object",
        "properties": {},
        "required": [],
    }
    if "type" not in schema:
        schema = {**schema, "type": "object"}

    meta = Tool(
        name=yuki_name,
        description=func.__doc__ or yuki_name,
        parameters=schema,
        func=func,
        needs_config=False,
    )

    # 避免重复注册（init 被调多次时）
    if yuki_name in _REGISTRY:
        return None
    register(meta)
    _registered_tool_names.add(yuki_name)
    return yuki_name


# ── server 初始化 / 清理 ───────────────────────────────────────────────

async def _start_one_server(name: str, cfg: dict[str, Any]) -> int:
    """启动一个 MCP server 子进程，握手，注册工具。返回注册的工具数。"""
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    cmd = (cfg.get("command") or "").strip()
    if not cmd:
        print(f"[mcp] {name}: 缺 command 字段，跳过", file=sys.stderr)
        return 0
    # 解析 command 绝对路径（frozen 模式 PATH 行为偶尔诡异，提前解析）
    resolved_cmd = shutil.which(cmd) or cmd
    args = list(cfg.get("args") or [])
    env_extra = cfg.get("env") or {}
    full_env = {**os.environ, **{str(k): str(v) for k, v in env_extra.items()}}
    cwd = cfg.get("cwd")

    params = StdioServerParameters(
        command=resolved_cmd,
        args=args,
        env=full_env,
        cwd=cwd if cwd else None,
    )

    # 把 (stdio_client + ClientSession) 两层 async context 入栈
    # 这样 shutdown() 能一次性 aclose，干净停子进程
    assert _exit_stack is not None
    try:
        read, write = await _exit_stack.enter_async_context(stdio_client(params))
        session = await _exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
    except Exception as e:
        print(f"[mcp] {name} 启动失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 0

    _sessions[name] = session

    # 列工具 + 注册
    try:
        list_result = await session.list_tools()
    except Exception as e:
        print(f"[mcp] {name} list_tools 失败: {e}", file=sys.stderr)
        return 0

    count = 0
    for mcp_tool in (list_result.tools or []):
        registered = _register_mcp_tool(name, session, mcp_tool)
        if registered:
            count += 1
    print(f"[mcp] {name} 连接成功，注册 {count} 个工具", file=sys.stderr)
    return count


async def init() -> dict[str, Any]:
    """启动所有 enabled 的 MCP server 并注册工具。被 FastAPI startup 调。

    幂等：重复调安全（用 _initialized 守卫）。
    """
    global _exit_stack, _initialized
    async with _init_lock:
        if _initialized:
            return {"already_initialized": True}
        _initialized = True

        cfg = _load_config()
        servers = cfg.get("mcpServers") or {}
        if not servers:
            return {"servers": 0, "tools": 0, "note": "mcp.json 缺失或无 server"}

        _exit_stack = AsyncExitStack()
        await _exit_stack.__aenter__()

        total_tools = 0
        server_count = 0
        for name, server_cfg in servers.items():
            if not isinstance(server_cfg, dict):
                continue
            # 默认 enabled=True，显式 false 才跳过
            if server_cfg.get("enabled") is False:
                continue
            server_count += 1
            tools_n = await _start_one_server(name, server_cfg)
            total_tools += tools_n

        return {"servers": server_count, "tools": total_tools}


async def shutdown() -> None:
    """关所有 MCP session + 子进程。被 FastAPI shutdown 调。"""
    global _exit_stack, _initialized
    # 注销注册过的工具，避免 hot-reload 时残留
    for name in list(_registered_tool_names):
        _REGISTRY.pop(name, None)
    _registered_tool_names.clear()
    _sessions.clear()
    if _exit_stack is not None:
        try:
            await _exit_stack.__aexit__(None, None, None)
        except Exception:
            pass
        _exit_stack = None
    _initialized = False


# ── 调试 / 状态查询 ────────────────────────────────────────────────────

def get_status() -> dict[str, Any]:
    """快速看一眼当前 MCP 桥接状态。"""
    return {
        "initialized": _initialized,
        "config_path": str(_config_path()),
        "config_exists": _config_path().is_file(),
        "active_servers": list(_sessions.keys()),
        "registered_tools_count": len(_registered_tool_names),
        "registered_tools": sorted(_registered_tool_names),
    }
