"""Agent ReAct loop —— 取代 ``langgraph.prebuilt.create_react_agent``。

设计本质就是 while 循环：

1. 用当前 message 历史调一次 LLM（流式）
2. LLM 没产出 tool_calls → 结束
3. LLM 产出 tool_calls → 执行每个工具 → 把结果作为 ``role="tool"``
   的消息追加进历史 → 回到 1

整个循环用 `asyncio` 异步驱动；工具函数同步/异步都支持。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable

from .llm import DeepSeekClient
from .messages import Message, ToolCall
from .tools import Tool

DEFAULT_MAX_ITERATIONS = 60  # 防 LLM 死循环调工具（20→60，给复杂多步任务空间）
WARNING_REMAINING_THRESHOLD = 5  # 剩余 ≤ 此值时往历史里 inject 系统提醒

# 工具调用事件 hook 签名：(event_dict, config_dict) -> None
# event_dict 至少含 phase("before"/"after"), tool, args, tool_call_id；
# after 阶段还会含 result_preview, duration_ms, ok。
# config_dict 是 agent.astream 调用方传入的 config（含 thread_id 等）。
ToolEventHook = Callable[[dict[str, Any], dict[str, Any]], None]


class Agent:
    """ReAct agent。

    使用示例::

        agent = Agent(llm=DeepSeekClient(), tools=list_tools(), system_prompt="...")
        history = [Message.user("帮我画个图")]
        config = {"thread_id": "...", "workdir": "..."}
        async for ev in agent.astream(history, config=config):
            ...

    事件协议（``astream`` 的 yield）：

    - ``{"type": "delta",    "text": str}``                    透传 LLM 文本块
    - ``{"type": "reasoning","text": str}``                    透传 LLM 思考块
    - ``{"type": "tool_call","id": str, "name": str}``         开始执行某工具（UI 提示用）
    - ``{"type": "tool_result", "id": str, "name": str, "preview": str}``
      工具执行完毕（debug 用，preview 截断到 200 字）
    - ``{"type": "done",     "new_messages": list[Message]}``  整轮 agent 完成，
      `new_messages` 为本轮新增的全部消息（assistant + tool_results），调用方
      应把它追加到自己维护的 history 并持久化。
    - ``{"type": "error",    "error": str}``                   LLM 或工具异常
    """

    def __init__(
        self,
        llm: DeepSeekClient,
        tools: list[Tool] | None = None,
        system_prompt: str = "",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        on_tool_event: ToolEventHook | None = None,
    ) -> None:
        """
        Args:
            tools: 工具列表。None 时**每次 astream 都从全局 `_REGISTRY` 动态拉取**，
                这样运行时 `define_skill` 注册的新工具立即可用；传入具体列表则
                绑死该列表（适合测试或受限场景）。
            on_tool_event: 工具调用事件 hook，每次工具调用前后会被同步调用一次。
                用于审计日志、用量统计等横切关注点。框架本身不持有"日志在哪存"的
                知识 —— 由调用方注入（见 ``audit.py``）。
                Hook 必须吞掉自己的异常，否则会让工具调用整体挂掉。
        """
        self.llm = llm
        self._fixed_tools = tools           # None 表示动态
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.on_tool_event = on_tool_event

    @property
    def tools(self) -> list[Tool]:
        if self._fixed_tools is not None:
            return self._fixed_tools
        from .tools import list_tools
        return list_tools()

    def _build_tool_by_name(self) -> dict[str, Tool]:
        return {t.name: t for t in self.tools}

    async def astream(
        self,
        history: list[Message],
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式驱动一轮 agent loop。

        Args:
            history: 完整对话历史（**包含本轮的最新用户消息**）。Agent 不会
                自动追加 system prompt —— 内部会用 ``self.system_prompt`` 作
                为第一条消息发给 LLM，但**不会**进入 `new_messages`，调用方
                也不需要持久化它。
            config: 注入给工具的配置字典（如 ``{"workdir": "..."}``）。
                Tool 函数签名带 ``config`` 参数（``needs_config=True``）时，
                会自动接收。
        """
        config = config or {}
        # 每次 astream 重新 snapshot 一次工具集（动态模式下能感知 define_skill）
        current_tools = self.tools
        tool_by_name = {t.name: t for t in current_tools}
        # 内部 message 序列（含 system prompt），仅给 LLM 看
        msgs: list[Message] = [Message.system(self.system_prompt)] + list(history)
        # 本轮新增的消息（不含 system / 不含外部传入的 history），供调用方持久化
        new_messages: list[Message] = []
        # 即将撞 max_iterations 时是否已 inject 过提醒（每次 astream 只 inject 一次）
        warning_injected = False

        for _iteration in range(self.max_iterations):
            # ── 0. 即将撞墙时往历史里 inject 系统提醒（仅一次，不进 new_messages）──
            remaining = self.max_iterations - _iteration
            if remaining <= WARNING_REMAINING_THRESHOLD and not warning_injected:
                warn = Message.system(
                    f"⚠️ 系统提示：你已用 {_iteration}/{self.max_iterations} 轮工具调用，"
                    f"剩 {remaining} 轮。如果任务还没收尾，**立刻调 ask_user 申请扩展或决定收尾方案**，"
                    f"不要继续埋头调工具。撞墙后整段对话会保留（主人可点『继续』接续）。"
                )
                msgs.append(warn)
                warning_injected = True

            # ── 1. 调一次 LLM，累积 chunk ─────────────────────────────────
            current_text = ""
            current_reasoning: list[str] = []
            current_tool_calls: list[ToolCall] = []

            async for ev in self.llm.stream(msgs, tools=current_tools):
                etype = ev["type"]
                if etype == "delta":
                    current_text += ev["text"]
                    yield ev  # 直接透传
                elif etype == "reasoning":
                    current_reasoning.append(ev["text"])
                    yield ev
                elif etype == "tool_call":
                    tc = ToolCall(
                        id=ev["id"] or _fallback_id(),
                        name=ev["name"],
                        arguments=ev["arguments"],
                    )
                    current_tool_calls.append(tc)
                    yield {"type": "tool_call", "id": tc.id, "name": tc.name}
                elif etype == "done":
                    # llm 流自然结束，外层 async for 也会停
                    pass
                elif etype == "error":
                    yield ev
                    return

            # ── 2. 把本轮 assistant 消息加入历史 ───────────────────────────
            assistant_msg = Message.assistant(
                content=current_text,
                tool_calls=current_tool_calls,
                reasoning_content="".join(current_reasoning) or None,
            )
            msgs.append(assistant_msg)
            new_messages.append(assistant_msg)

            # ── 3. 没工具调用 → 终止循环 ──────────────────────────────────
            if not current_tool_calls:
                yield {"type": "done", "new_messages": new_messages}
                return

            # ── 4. 执行所有工具调用 ───────────────────────────────────────
            for tc in current_tool_calls:
                result_str = await self._invoke_tool(tc, config, tool_by_name)
                # 截断 preview 仅供 UI 显示；完整内容进入下一轮 LLM 输入
                yield {
                    "type": "tool_result",
                    "id": tc.id,
                    "name": tc.name,
                    "preview": result_str[:200],
                }
                tool_msg = Message.tool_result(tool_call_id=tc.id, content=result_str)
                msgs.append(tool_msg)
                new_messages.append(tool_msg)

            # 回到第 1 步，再问一次 LLM
        else:
            # 撞墙：循环正常走完没有自然终止 = 用完了 max_iterations
            # 关键改动（旧版本是 yield error 直接返回，导致 new_messages 不被持久化，
            # 19 轮对话凭空消失）：现在改为 yield done + truncated=True，让调用方
            # 1) 正常持久化 new_messages（断点续传的基础）
            # 2) 前端能展示"继续"按钮，用户点击后用现有 history 续跑
            yield {
                "type": "done",
                "new_messages": new_messages,
                "truncated": True,
                "reason": f"超过最大工具调用轮数 ({self.max_iterations})",
            }

    async def _invoke_tool(
        self,
        tc: ToolCall,
        config: dict[str, Any],
        tool_by_name: dict[str, Tool],
    ) -> str:
        """执行单个工具调用，返回字符串化的结果。异常被捕获后作为结果返回。

        如果 ``self.on_tool_event`` 不为 None，在调用前后各触发一次 hook。
        Hook 异常被吞掉，绝不影响工具主流程。
        """
        # before hook
        self._fire_hook({
            "phase": "before",
            "tool": tc.name,
            "args": tc.arguments,
            "tool_call_id": tc.id,
        }, config)

        tool = tool_by_name.get(tc.name)
        t0 = time.monotonic()
        if tool is None:
            result_str = f"错误：未知工具 {tc.name!r}（可用工具：{list(tool_by_name)})"
            ok = False
        else:
            try:
                kwargs = dict(tc.arguments)
                if tool.needs_config:
                    kwargs["config"] = {"configurable": config}
                if asyncio.iscoroutinefunction(tool.func):
                    result = await tool.func(**kwargs)
                else:
                    result = await asyncio.to_thread(tool.func, **kwargs)
                result_str = str(result) if result is not None else ""
                ok = True
            except Exception as e:  # noqa: BLE001
                result_str = f"工具执行异常 {type(e).__name__}: {e}"
                ok = False

        # after hook
        self._fire_hook({
            "phase": "after",
            "tool": tc.name,
            "args": tc.arguments,
            "tool_call_id": tc.id,
            "ok": ok,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "result_preview": result_str,
        }, config)

        return result_str

    def _fire_hook(self, event: dict[str, Any], config: dict[str, Any]) -> None:
        if self.on_tool_event is None:
            return
        try:
            self.on_tool_event(event, config)
        except Exception:  # noqa: BLE001
            # hook 异常永远不能影响主流程
            pass


def _fallback_id() -> str:
    """LLM 没给 tool_call_id 时的兜底 id。"""
    import uuid
    return f"local_{uuid.uuid4().hex[:12]}"
