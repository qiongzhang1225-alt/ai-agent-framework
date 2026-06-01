# 从 LangChain / LangGraph 迁移到自建 Agent 框架

> **迁移已完成（2026-05-27）**。LangChain / LangGraph 全部依赖已从代码与 `.venv` 中清除。
> 整个 stack 现在依靠 httpx 直连 DeepSeek API + 自建 `ai_agent/` 框架运行。
> 提交历史：
> - Phase 1 `f4a3b9e` — 消息类型 + 工具装饰器
> - Phase 2 `09968f9` — DeepSeek 流式客户端
> - Phase 3 `69d141d` — 自建 ReAct loop（agent.py + server.py 大改）
> - Phase 4 `3dd977b` — JSONCheckpoint 抽出独立模块
> - Phase 5 `e75f11b` — 拆除 @tool 桥接 + 卸包
>
> 本文档保留作为"决策与设计"参考。如需追溯每个 Phase 的具体改动，看对应 git commit。

---

## 1. 当前 LangChain / LangGraph 真正用了什么

盘点的结果：实际依赖**比想象的薄**。

### agent.py（核心，~430 行）

| 用法 | 来源 | 实际承担的工作 |
|---|---|---|
| `ChatDeepSeek` | `langchain_deepseek` | 调 DeepSeek OpenAI 兼容 API（流式 + tool calling） |
| `_PatchedChatDeepSeek`（子类） | 自己写 | 修复上游 bug：thinking 模式下 `reasoning_content` 没回传 |
| `@tool` 装饰器（7 处） | `langchain_core.tools` | 把 Python 函数转成 LLM 可调用的工具 + 自动从 docstring/类型注解生成 JSON schema |
| `HumanMessage` / `AIMessage` | `langchain_core.messages` | 标准化的消息对象 |
| `RunnableConfig`（1 处） | `langchain_core.runnables` | 给 `execute_code` 注入会话级的 `workdir` |
| `create_react_agent` | `langgraph.prebuilt` | ReAct 循环：think → tool call → observe → think → ... |
| `MemorySaver` | `langgraph.checkpoint.memory` | fallback 用的内存 checkpoint |

### server.py（~440 行）

| 用法 | 来源 | 实际承担的工作 |
|---|---|---|
| `HumanMessage` | `langchain_core.messages` | 构造用户消息 |
| `AsyncSqliteSaver` | `langgraph.checkpoint.sqlite.aio` | LangGraph 上下文持久化到 SQLite |
| `metadata["langgraph_node"]` | LangGraph 内部约定 | 区分流的来源是 LLM 还是工具 |

**就这些**。没用 RAG chain、AgentExecutor、PromptTemplate、Output Parser 等 LangChain "高级"组件。

---

## 2. 迁移的真实收益与代价

### 收益

| 类别 | 具体 |
|---|---|
| **依赖瘦身** | 干掉 5 个包：`langchain`、`langchain-core`、`langchain-openai`、`langchain-community`、`langchain-deepseek`、`langgraph`、`langgraph-checkpoint`、`langgraph-checkpoint-sqlite`（实际是 8 个）；约 -50MB 安装体积 |
| **启动加速** | 少 1-2 秒（LangChain 启动时 import 不少东西） |
| **调试 stack 变短** | 报错栈往往穿过 5-6 层 LangChain 包装；自建后是直栈 |
| **完全自由** | 想给 tool call 加重试 / 缓存 / 日志 / 审计，加在 agent loop 里即可，不用绕过 LangGraph 的抽象 |
| **不再受上游影响** | LangChain 改 API 不影响你（langchain-deepseek 已经踩过 reasoning_content bug） |

### 代价

| 类别 | 具体 |
|---|---|
| **时间投入** | 我估约 **12-20 小时**纯写代码 + 调试 |
| **新代码量** | ~600-900 行（替换 LangChain 的 ~50 行间接调用） |
| **新 bug 风险** | 自建组件初期会有 edge case（比如流式包含 tool_call 与 text 混合的解析） |
| **失去未来便利** | 想加 RAG chain、Reranker、Agent Executor 等高级用法时，要自己实现而不是 import 一下 |
| **学习成本** | 你（和我）需要熟悉 DeepSeek API 的 raw 协议、tool calling 的 JSON 格式细节 |

### 真的需要现在迁吗？

| 信号 | 现状 |
|---|---|
| LangChain 阻止你实现某个功能 | ❌ 没有 |
| LangChain bug 经常拖累你 | ❌ 就 1 个 reasoning_content，已 patch |
| LangChain 升级破坏你的代码 | ❌ 没遇到 |
| LangChain 的抽象让代码难懂 | ⚠️ 有一点（RunnableConfig 注入 workdir 那个不直观） |
| 想要做"非主流"工具调用模式 | ❌ 当前 ReAct 就够 |
| 想要细粒度优化（缓存、批处理） | ❌ 还没到那个规模 |

**5 个信号里只有 1 个"有一点"。** 这就是为什么我说"非必需"。

但**为了练手 / 长期掌控**也是合理的动机，下面给出可执行计划。

---

## 3. 目标架构

```
ai_agent/                  ← 新建：自建框架（替换 LangChain）
├── __init__.py
├── messages.py            ← 替换 HumanMessage / AIMessage
├── tools.py               ← 替换 @tool 装饰器
├── llm.py                 ← 替换 ChatDeepSeek（直接 httpx）
├── loop.py                ← 替换 create_react_agent
└── persist.py             ← 替换 AsyncSqliteSaver

agent.py                   ← 业务工具仍在这里（execute_code 等）
                              + create_agent 改用 ai_agent 包
server.py                  ← 几乎不动，只换 import
prompts/yuki.md            ← 不动
```

模块边界设计：

```
┌──────────────┐
│ server.py    │  ← Web 层
│ (FastAPI)    │
└──────┬───────┘
       │ uses
       ↓
┌──────────────┐       ┌──────────────┐
│ agent.py     │──────→│ ai_agent/    │
│ (business    │       │ (framework)  │
│  tools +     │       └──────────────┘
│  config)     │              │
└──────────────┘              ↓
                       ┌──────────────┐
                       │ DeepSeek API │
                       └──────────────┘
```

---

## 4. 五阶段迁移计划

### 总体原则

1. **每个 Phase 都能独立验证、独立部署** —— 不让项目长时间处于半坏状态
2. **保留旧的 LangChain 实现** 直到新实现验证通过（用 git branch 或者保留旧文件 `.bak`）
3. **API 兼容** —— 内部替换，外部接口（`create_agent`、tool 定义、消息流式协议）保持不变
4. **每个 Phase 完成后跑一遍冒烟测试**：发普通消息、用 execute_code 画图、跨对话 recall

### Phase 1：消息类型 + 工具装饰器（最简单，~2-3h）

**目标**：取代 `HumanMessage` / `AIMessage` / `@tool`，但工具的 Python 函数内容不变。

**新建文件**：

```python
# ai_agent/messages.py
from dataclasses import dataclass, field
from typing import Literal, Any

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None   # role=="tool" 时才有
    reasoning_content: str | None = None  # DeepSeek thinking mode 的字段

    def to_openai(self) -> dict:
        """转成 OpenAI 兼容 API 的请求体格式。"""
        d: dict = {"role": self.role, "content": self.content}
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
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.reasoning_content:
            d["reasoning_content"] = self.reasoning_content  # 关键！
        return d
```

```python
# ai_agent/tools.py
import inspect
from dataclasses import dataclass
from typing import Callable, Any, get_type_hints


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict           # JSON Schema
    func: Callable
    needs_config: bool = False  # 是否有 config 参数（注入 workdir 等）


_REGISTRY: dict[str, Tool] = {}


def tool(func: Callable) -> Callable:
    """把函数注册为 LLM 可调用工具。从签名+docstring 自动生成 JSON schema。"""
    sig = inspect.signature(func)
    hints = get_type_hints(func)
    doc = (func.__doc__ or "").strip()
    first_line = doc.split("\n", 1)[0]

    props = {}
    required = []
    needs_config = False
    for name, param in sig.parameters.items():
        if name == "config":
            needs_config = True
            continue
        py_type = hints.get(name, str)
        json_type = {str: "string", int: "integer", float: "number", bool: "boolean"}.get(py_type, "string")
        props[name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema = {
        "type": "object",
        "properties": props,
        "required": required,
    }
    _REGISTRY[func.__name__] = Tool(
        name=func.__name__,
        description=first_line if first_line else func.__name__,
        parameters=schema,
        func=func,
        needs_config=needs_config,
    )
    return func


def list_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)
```

**替换方式**：
- agent.py 顶部 `from langchain_core.tools import tool` → `from ai_agent.tools import tool`
- 所有 `@tool` 不动，写法兼容
- `RunnableConfig` 类型注解换成 `dict | None`，含义不变

**测试**：
```bash
python -c "import ai_agent.tools; from agent import tools; print([t.name for t in ai_agent.tools.list_tools()])"
```
应该列出全部 7 个工具。

**回滚**：把 import 改回 `langchain_core.tools` 即可。

---

### Phase 2：DeepSeek LLM 客户端（中等，~3-4h）

**目标**：取代 `ChatDeepSeek` + `_PatchedChatDeepSeek`。直接用 `httpx` 调 DeepSeek API。

**关键挑战**：
- 流式（SSE）解析
- tool_calls 在流中是**分块下发**的（OpenAI 协议规定 `tool_calls[].function.arguments` 是字符串，会逐块拼接）
- `reasoning_content` 字段处理

**新建文件**：

```python
# ai_agent/llm.py
import os, json
from typing import AsyncIterator
import httpx
from .messages import Message, ToolCall
from .tools import Tool

DEEPSEEK_BASE = "https://api.deepseek.com/v1"


class DeepSeekClient:
    def __init__(self, model: str, api_key: str | None = None, temperature: float = 0.7):
        self.model = model
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.temperature = temperature
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10))

    async def stream(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
    ) -> AsyncIterator[dict]:
        """流式调用 DeepSeek。yield 事件 dict，结构：
          {"type": "delta", "text": "..."}                      # 普通文字
          {"type": "tool_call", "id": "...", "name": "...", "arguments": {...}}  # 完整的工具调用
          {"type": "done", "finish_reason": "..."}              # 流结束
        """
        payload = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with self._client.stream(
            "POST", f"{DEEPSEEK_BASE}/chat/completions", json=payload, headers=headers
        ) as resp:
            resp.raise_for_status()
            # 累积器：流中 tool_calls 是分片到达的
            tool_buffers: dict[int, dict] = {}
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = chunk["choices"][0]
                delta = choice.get("delta", {})

                # 普通文字
                if (text := delta.get("content")):
                    yield {"type": "delta", "text": text}

                # tool_calls 分片
                for tc_delta in (delta.get("tool_calls") or []):
                    idx = tc_delta["index"]
                    buf = tool_buffers.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if "id" in tc_delta:
                        buf["id"] = tc_delta["id"]
                    if (fn := tc_delta.get("function")):
                        if "name" in fn:
                            buf["name"] += fn["name"]
                        if "arguments" in fn:
                            buf["arguments"] += fn["arguments"]

                if choice.get("finish_reason"):
                    # 流结束：把累积的 tool_calls 解析并 yield
                    for idx, buf in sorted(tool_buffers.items()):
                        try:
                            args = json.loads(buf["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        yield {
                            "type": "tool_call",
                            "id": buf["id"],
                            "name": buf["name"],
                            "arguments": args,
                        }
                    yield {"type": "done", "finish_reason": choice["finish_reason"]}
                    return
```

**测试**：写一个最小脚本调用 `DeepSeekClient.stream(...)`，确认能正常拿到 delta + tool_call + done 三种事件。

**与 LangChain 行为对齐的关键**：把 reasoning_content 也累积到 Message 中，下一轮请求时通过 `to_openai()` 传回 —— 这就替代了我们的 `_PatchedChatDeepSeek` 补丁。

**回滚**：保留 `agent.py` 中的 `_PatchedChatDeepSeek`，create_agent 仍用 LangChain 版本。Phase 2 完成时新增一个 `create_agent_v2(...)` 与旧版并存，逐渐切换。

---

### Phase 3：Agent ReAct Loop（核心，~4-6h）

**目标**：取代 `create_react_agent`。自己写一个 while 循环。

**新建文件**：

```python
# ai_agent/loop.py
import asyncio, uuid
from typing import AsyncIterator
from .messages import Message, ToolCall
from .llm import DeepSeekClient
from .tools import Tool, get_tool

MAX_ITERATIONS = 20  # 防止 LLM 死循环调工具


class Agent:
    def __init__(self, llm: DeepSeekClient, tools: list[Tool], system_prompt: str):
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt

    async def astream(
        self,
        history: list[Message],          # 包含本轮 user 消息的完整历史
        config: dict | None = None,      # 注入给工具（workdir 等）
    ) -> AsyncIterator[dict]:
        """流式驱动一轮 agent loop。yield 事件：
            {"type": "delta", "text": "..."}
            {"type": "tool_call", "name": "..."}      # UI 提示用
            {"type": "tool_result", "name": "...", "result": "..."}  # 调试用
            {"type": "done", "message": Message}      # 最终的 assistant 消息
        """
        # 把 system_prompt 放在最前
        msgs = [Message(role="system", content=self.system_prompt)] + history

        for iteration in range(MAX_ITERATIONS):
            # 调一次 LLM
            current_text = ""
            current_tool_calls: list[ToolCall] = []
            async for event in self.llm.stream(msgs, tools=self.tools):
                if event["type"] == "delta":
                    current_text += event["text"]
                    yield event
                elif event["type"] == "tool_call":
                    tc = ToolCall(
                        id=event["id"] or uuid.uuid4().hex,
                        name=event["name"],
                        arguments=event["arguments"],
                    )
                    current_tool_calls.append(tc)
                    yield {"type": "tool_call", "name": tc.name}
                elif event["type"] == "done":
                    finish = event["finish_reason"]

            # 构造本轮的 assistant 消息
            assistant_msg = Message(
                role="assistant",
                content=current_text,
                tool_calls=current_tool_calls,
            )
            msgs.append(assistant_msg)

            # 没有工具调用 → 终止循环
            if not current_tool_calls:
                yield {"type": "done", "message": assistant_msg}
                return

            # 有工具调用 → 执行每个工具，把结果作为 "tool" 消息接回
            for tc in current_tool_calls:
                tool_obj = get_tool(tc.name)
                if tool_obj is None:
                    result = f"错误：未知工具 {tc.name}"
                else:
                    try:
                        kwargs = dict(tc.arguments)
                        if tool_obj.needs_config:
                            kwargs["config"] = config or {}
                        # 工具可能是同步或异步
                        if asyncio.iscoroutinefunction(tool_obj.func):
                            result = await tool_obj.func(**kwargs)
                        else:
                            result = await asyncio.to_thread(tool_obj.func, **kwargs)
                    except Exception as e:
                        result = f"工具执行异常：{e}"

                yield {"type": "tool_result", "name": tc.name, "result": str(result)[:200]}
                msgs.append(Message(
                    role="tool",
                    content=str(result),
                    tool_call_id=tc.id,
                ))

        # 超过最大轮数仍未结束
        yield {"type": "error", "message": "超过最大工具调用轮数"}
```

**核心要点**：
- 这个 while 循环就是 ReAct 的全部
- 工具同步/异步都支持（用 `asyncio.iscoroutinefunction` 判断）
- config 注入（workdir）通过 `needs_config` 显式判断，比 LangChain 的 `RunnableConfig` 注解更直白

**测试**：写一个测试脚本，让 agent 用 calculate 工具算 `2 ** 10 + 3`，看是否正确循环。

---

### Phase 4：状态持久化（中等，~2-3h）

**目标**：取代 `AsyncSqliteSaver`。

由于 LangGraph 的 checkpoint 在我们这里**只用了"消息历史"**这一项功能（没用到 langgraph 的 graph state、interrupts 等），可以**直接简化成 JSON**：

```python
# ai_agent/persist.py
import json
from dataclasses import asdict
from pathlib import Path
from .messages import Message, ToolCall


class JSONCheckpoint:
    """每个 thread_id 一个 JSON 文件，存完整消息历史。"""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, thread_id: str) -> Path:
        return self.root / f"{thread_id}.json"

    def save(self, thread_id: str, messages: list[Message]) -> None:
        data = [asdict(m) for m in messages]
        # 原子写
        path = self._path(thread_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def load(self, thread_id: str) -> list[Message]:
        path = self._path(thread_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            Message(
                role=d["role"],
                content=d.get("content", ""),
                tool_calls=[ToolCall(**tc) for tc in d.get("tool_calls") or []],
                tool_call_id=d.get("tool_call_id"),
                reasoning_content=d.get("reasoning_content"),
            )
            for d in data
        ]

    def delete(self, thread_id: str) -> None:
        path = self._path(thread_id)
        if path.exists():
            path.unlink()
```

**收益**：
- 比 SQLite 简单很多（看得见摸得着）
- 不再需要 lifespan + aiosqlite
- 调试时直接打开 JSON 文件看消息

**测试**：保存一组消息，重新加载，对比内容是否一致。

---

### Phase 5：清理 LangChain 依赖（小，~1-2h）

**目标**：彻底删掉 `langchain*` 和 `langgraph*` 包。

步骤：
1. **agent.py** 顶部所有 `from langchain*` / `from langgraph*` 删除
2. **server.py** 同上
3. **requirements.txt** 删除：
   ```
   langchain
   langchain-openai
   langchain-deepseek
   langchain-community
   langgraph
   langgraph-checkpoint-sqlite
   ```
   保留 `chromadb`（向量库还是要的，但记忆模块本身不依赖 LangChain）
4. **重装环境**：删除 `.venv`，重建并装新的 requirements
5. **完整跑一遍**：发消息 / 执行代码 / 记忆 read/write / 重启恢复 / 跨对话 recall

---

## 5. 风险与回滚预案

### 风险点

| 风险 | 缓解措施 |
|---|---|
| 流式 tool_call 分片解析出 bug（参数 JSON 解析错误） | 在 Phase 2 增加单元测试，喂一段录制好的 SSE 流验证 |
| DeepSeek API 微小协议差异（比如 reasoning_content 字段名变化） | Phase 2 完成时先用旧消息历史（来自 LangGraph 版本）验证向后兼容 |
| 工具的同步/异步调用错误（execute_code 是同步） | Phase 3 在 loop 中显式用 `asyncio.iscoroutinefunction` 判断 |
| 持久化格式变更导致老对话不能加载 | Phase 4 提供一个 `migrate.py` 把 LangGraph SQLite 中的消息导出成 JSON |

### 回滚策略

**每个 Phase 完成时打一个 git tag**：

```bash
git tag pre-migration            # 迁移前
git tag phase-1-tools            # Phase 1 完成
git tag phase-2-llm              # ...
```

任何一步出问题，`git reset --hard <上一个 tag>` 即可。

**或者更稳的**：每个 Phase 在一个独立 branch 做完后 merge，主分支始终是"已知能跑"状态。

---

## 6. 不迁移的替代方案

如果只是某个具体地方让你不爽，可以**针对性消除**，不必整体迁移：

| 现在的小痛点 | 局部解药 |
|---|---|
| `RunnableConfig` 注入 workdir 不直观 | 用 `contextvars` 全局上下文替代，几行代码 |
| `_PatchedChatDeepSeek` 是 monkey-patch | 给 langchain-deepseek 提 PR 修上游 |
| LangGraph checkpoint 黑盒 | 在 server.py 增加导出/导入端点，按需 dump 出来看 |
| LangGraph SqliteSaver 表名变动让 DELETE 不稳 | 把"删除时清理 checkpoint"改成"软删除"，标记 thread_id 已废弃 |

---

## 7. 决策辅助

回答以下问题，再决定是否启动迁移：

1. **现在做这件事，能不能接受暂停其他功能开发 12-20 小时？**
   - 不能 → 先用着 LangChain，遇到具体限制再迁
   - 能 → 继续

2. **你最近 30 天有没有遇到 LangChain 阻止你做某个事？**
   - 没有 → 迁移收益不明显
   - 有 → 哪个事？是否能用局部解药解决？

3. **你对 OpenAI 兼容 API 的协议细节熟悉吗？**（tool_calls 在流中如何分片、reasoning_content 何时出现、stop_reason 都有哪些值）
   - 不熟 → Phase 2 要先花 2-3h 读文档
   - 熟 → 直接开干

4. **如果迁移后 1 个月发现某个 LangChain 高级特性突然有用了**（比如你想接 Reranker），会不会后悔？
   - 会 → 缓一缓
   - 不会，自己写 → 走起

---

## 8. 我的推荐节奏

如果你确定要迁移：

| 时间 | 动作 |
|---|---|
| **今天** | 仔细看本文档，决定是否启动 |
| **如果决定启动** | 先 `git tag pre-migration` 留个底 |
| **第 1 天（2-3h）** | Phase 1：消息类型 + 工具装饰器 |
| **第 1 天 / 第 2 天（3-4h）** | Phase 2：DeepSeek 客户端，调通流式 + tool calling |
| **第 2 天 / 第 3 天（4-6h）** | Phase 3：Agent loop |
| **第 3 天（2-3h）** | Phase 4：JSON checkpoint |
| **第 4 天（1-2h）** | Phase 5：清理依赖 + 完整跑一遍 |

期间任何 Phase 卡住超过预期 50%，停下来评估是否继续。

---

## 9. 启动条件

**当你确定要做时，告诉我"开始 Phase 1"**。我会：

1. 先按本文档建立 `ai_agent/` 包骨架（空文件 + 注释）
2. 实施 Phase 1：写 `messages.py` 和 `tools.py`
3. 替换 `agent.py` 的 import，验证 7 个工具仍然能正常注册和被调用
4. 端到端测试：发消息 → execute_code → 重启 → 跨对话 recall，全部通过
5. 报告完成 + 准备 Phase 2

如果不做：本文档作为**未来参考**保留在仓库里。任何时候你想做，重新读一遍即可。

---

## 附录：当前 LangChain 用法的精确清单

```text
agent.py:
  L13  from langchain_deepseek import ChatDeepSeek
  L14  from langchain_core.messages import HumanMessage, AIMessage
  L15  from langchain_core.tools import tool
  L16  from langchain_core.runnables import RunnableConfig
  L20  from langgraph.prebuilt import create_react_agent
  L21  from langgraph.checkpoint.memory import MemorySaver
  L48  @tool   (calculate)
  L58  @tool   (get_current_datetime)
  L65  @tool   (web_search)
  L75  @tool   (fetch_webpage)
  L169 @tool   (execute_code, 唯一一个用 config: RunnableConfig 的工具)
  L259 @tool   (remember)
  L288 @tool   (recall)
  L383 class _PatchedChatDeepSeek(ChatDeepSeek)  ← Phase 2 后删除
  L428 create_react_agent(...)                    ← Phase 3 后删除

server.py:
  L28  from langchain_core.messages import HumanMessage
  L29  from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
  L56  AsyncSqliteSaver(_aio_conn)
  L326 HumanMessage(content=req.message)
  L330 metadata.get("langgraph_node")             ← 改成自建框架的事件类型
```

替换映射：

| LangChain | 自建 |
|---|---|
| `ChatDeepSeek` | `ai_agent.llm.DeepSeekClient` |
| `HumanMessage(content=x)` | `Message(role="user", content=x)` |
| `AIMessage` | `Message(role="assistant", ...)` |
| `@tool` | `from ai_agent.tools import tool`（写法不变） |
| `RunnableConfig` | `dict`（含义不变） |
| `create_react_agent(llm, tools, prompt, checkpointer)` | `Agent(llm, tools, system_prompt).astream(history, config)` |
| `AsyncSqliteSaver` | `JSONCheckpoint` |
| `metadata["langgraph_node"]` | 事件 `type` 字段（`delta` / `tool_call` / `done`） |

---

**文档版本**：v1
**最后更新**：随 Phase 完成时更新进度表
