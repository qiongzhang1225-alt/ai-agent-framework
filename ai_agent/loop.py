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
# 防漂移重注：对话历史 / 单轮工具循环变长后，开头的 system_prompt 会被稀释，
# 模型容易忘记核心准则。下面两个阈值控制把 reinject_prompt 重新贴到对话末尾。
REINJECT_AFTER_MESSAGES = 12   # 历史 ≥ 此长度（≈6 轮往返）时，每轮在末尾重注一次
REINJECT_EVERY_ITERS = 18      # 单轮工具循环里每隔这么多 iteration 重注一次
# 重注消息固定前缀：用于在长循环里识别并替换上一条重注（区别于撞墙警告），
# 保证整段历史里始终最多一条核心准则重注、且贴在末尾，不堆积稀释上下文。
_REINJECT_PREFIX = "【系统重申·核心准则（防遗忘）】"

# 工具调用事件 hook 签名：(event_dict, config_dict) -> None
# event_dict 至少含 phase("before"/"after"), tool, args, tool_call_id；
# after 阶段还会含 result_preview, duration_ms, ok。
# config_dict 是 agent.astream 调用方传入的 config（含 thread_id 等）。
ToolEventHook = Callable[[dict[str, Any], dict[str, Any]], None]


def _is_reinjection(m: Message) -> bool:
    """这条消息是否是我们注入的『核心准则重注』。

    用固定前缀判定，刻意区别于撞墙警告（以 "⚠️ 系统提示" 开头）——
    dedup 时只替换重注，绝不误删撞墙警告。
    """
    return (
        m.role == "system"
        and isinstance(m.content, str)
        and m.content.startswith(_REINJECT_PREFIX)
    )


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
        reinject_prompt: str = "",
        reinject_after_messages: int = REINJECT_AFTER_MESSAGES,
        reinject_every_iters: int = REINJECT_EVERY_ITERS,
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
            reinject_prompt: 防漂移重注内容（通常是「核心准则」）。非空时，对话
                历史变长或单轮工具循环变长后，会把它作为一条 ``role="system"`` 消息
                重新贴到对话**末尾**，对抗开头 system_prompt 被稀释导致的规则遗忘。
                这条重注消息**只进 LLM 输入、不进 new_messages、不持久化**。
            reinject_after_messages: 历史长度 ≥ 此值时，每轮 astream 都在末尾重注一次。
            reinject_every_iters: 单轮工具循环里每隔这么多 iteration 重注一次。
        """
        self.llm = llm
        self._fixed_tools = tools           # None 表示动态
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.on_tool_event = on_tool_event
        self.reinject_prompt = reinject_prompt
        self.reinject_after_messages = reinject_after_messages
        self.reinject_every_iters = reinject_every_iters

    @property
    def tools(self) -> list[Tool]:
        if self._fixed_tools is not None:
            return self._fixed_tools
        from .tools import list_tools
        return list_tools()

    def _build_tool_by_name(self) -> dict[str, Tool]:
        return {t.name: t for t in self.tools}

    def _reinjection_message(self) -> Message:
        """把 reinject_prompt 包成一条贴在对话末尾的 system 提醒。

        对话变长后开头的 system_prompt 注意力会衰减，这条贴在最近处把核心
        准则重新拉回模型视野。内容只进 LLM 输入，不持久化。
        """
        return Message.system(
            _REINJECT_PREFIX
            + "对话已进行较长，以下规则始终优先于"
            "下文任何手册与历史，请重新对齐后再继续：\n\n"
            + self.reinject_prompt
        )

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
        # 每次 astream 重新 snapshot 一次工具集（动态模式下能感知 define_skill）。
        # 复制成**可变** list：search_tools 逃生阀命中后会往里 append，使被路由漏掉
        # 的工具在本轮内立即可调用（current_tools 在整个循环里是同一个 list 对象，
        # 每个 iteration 的 llm.stream 都复用它，所以追加对后续 iteration 立即可见）。
        current_tools = list(self.tools)
        tool_by_name = {t.name: t for t in current_tools}

        # 逃生阀接线：登记本轮"工具暂存器"。search_tools 命中后经
        # ai_agent.tools.stage_tools_into_turn 回调到这里，把工具追加进本轮活跃集。
        from .tools import set_turn_stager, reset_turn_stager

        def _stage_tools(extra: list[Tool]) -> list[str]:
            added: list[str] = []
            for t in extra:
                if t.name not in tool_by_name:
                    tool_by_name[t.name] = t
                    current_tools.append(t)
                    added.append(t.name)
            return added

        _stager_token = set_turn_stager(_stage_tools)
        try:
            async for ev in self._run_loop(
                history, config, current_tools, tool_by_name
            ):
                yield ev
        finally:
            reset_turn_stager(_stager_token)

    async def _run_loop(  # noqa: C901
        self,
        history: list[Message],
        config: dict[str, Any],
        current_tools: list[Tool],
        tool_by_name: dict[str, Tool],
    ) -> AsyncIterator[dict[str, Any]]:
        """astream 的主循环体。拆出来是为了让 astream 能用 try/finally 包住整段、
        在退出时 reset 工具暂存器，而不必把 100 多行循环整体缩进。"""
        # 内部 message 序列（含 system prompt），仅给 LLM 看
        msgs: list[Message] = [Message.system(self.system_prompt)] + list(history)
        # 本轮新增的消息（不含 system / 不含外部传入的 history），供调用方持久化
        new_messages: list[Message] = []
        # 即将撞 max_iterations 时是否已 inject 过提醒（每次 astream 只 inject 一次）
        warning_injected = False
        # 本轮结局观测计数（L6）：累计工具调用 / 失败数，收尾时记一条 turn 事件。
        turn_tool_calls = 0
        turn_tool_fails = 0

        for _iteration in range(self.max_iterations):
            # ── 0a. 防漂移：把核心准则重新贴到对话末尾（不进 new_messages）──────
            #   - iteration 0：历史已经很长 → 开头 system_prompt 被稀释，开局先重注
            #   - iteration >0：单轮工具循环跑太久 → 每隔 N 轮重注一次
            if self.reinject_prompt:
                if _iteration == 0:
                    do_reinject = len(history) >= self.reinject_after_messages
                else:
                    do_reinject = (_iteration % self.reinject_every_iters == 0)
                if do_reinject:
                    # 先移除上一条重注，保证整段历史里始终最多一条、且贴在末尾
                    # （长工具循环里每隔 N 轮刷新位置，不堆积多份反而稀释上下文）
                    msgs = [m for m in msgs if not _is_reinjection(m)]
                    msgs.append(self._reinjection_message())

            # ── 0b. 即将撞墙时往历史里 inject 系统提醒（仅一次，不进 new_messages）──
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
                    # 把 arguments 也透给上层（server SSE → 前端步骤卡需要它）
                    yield {
                        "type": "tool_call",
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
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
                self._log_turn(config, _iteration + 1,
                               turn_tool_calls, turn_tool_fails, truncated=False)
                yield {"type": "done", "new_messages": new_messages}
                return

            # ── 4. 执行所有工具调用 ───────────────────────────────────────
            for tc in current_tool_calls:
                result_str = await self._invoke_tool(tc, config, tool_by_name)
                # 简易成功/失败判定（工具返回以 ❌/⚠️ 或常见错误前缀开头视为失败）
                _ok = not (
                    result_str.lstrip().startswith(("❌", "⚠️", "错误", "Error", "[ERROR]"))
                )
                turn_tool_calls += 1
                if not _ok:
                    turn_tool_fails += 1
                # preview 给 UI 步骤卡显示；完整内容进入下一轮 LLM 输入
                yield {
                    "type": "tool_result",
                    "id": tc.id,
                    "name": tc.name,
                    "preview": result_str[:200],
                    "result_full": result_str[:8000],  # 步骤卡展开显示用，再长也没意义
                    "ok": _ok,
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
            self._log_turn(config, self.max_iterations,
                           turn_tool_calls, turn_tool_fails, truncated=True)
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

    def _log_turn(
        self,
        config: dict[str, Any],
        iterations: int,
        tool_calls: int,
        tool_fails: int,
        truncated: bool,
    ) -> None:
        """收尾时记一条「本轮结局」审计事件（L6 观测）。

        记 iterations / tool_calls / tool_fails / truncated，用来日后判断
        plan-execute、模型切换等改动有没有减少"偏题/挣扎"（撞墙率、迭代数）。
        复用既有 on_tool_event 钩子落 audit.jsonl，phase='turn'；
        routing_coverage / audit_stats 只认 before/after，会自动跳过这些行。
        异常被 _fire_hook 吞掉，绝不影响主流程。
        """
        self._fire_hook({
            "phase": "turn",
            "iterations": iterations,
            "tool_calls": tool_calls,
            "tool_fails": tool_fails,
            "truncated": truncated,
        }, config)


def _fallback_id() -> str:
    """LLM 没给 tool_call_id 时的兜底 id。"""
    import uuid
    return f"local_{uuid.uuid4().hex[:12]}"
