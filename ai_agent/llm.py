"""DeepSeek LLM 客户端（OpenAI 兼容 API + 异步流式 + tool calling）。

将取代 `langchain_deepseek.ChatDeepSeek` 以及我们自己 patch 的
`_PatchedChatDeepSeek` —— 后者只是修复了"reasoning_content 没传回"的 bug，
这里在 `Message.to_openai()` 中**内建**了正确行为。

Phase 2 范围：仅建立此客户端 + 单独测试通过；
**不接入 `agent.py`**（LangGraph 主循环仍用 `_PatchedChatDeepSeek`）。
Phase 3 自建 ReAct loop 时才接入。

事件协议（`stream` 的 yield）：

- ``{"type": "delta", "text": str}``        — 普通回复文字 chunk
- ``{"type": "reasoning", "text": str}``    — thinking 模式下的推理过程 chunk
- ``{"type": "tool_call", "id": str, "name": str, "arguments": dict}``
  — 一次完整解析后的工具调用（流中是分片到达的，我们累积后再 yield）
- ``{"type": "done", "finish_reason": str, "reasoning_full": str | None}``
  — 流结束，附完整 reasoning_content（用于回传给下一轮）
- ``{"type": "error", "error": str}``       — 出错（HTTP / JSON / 其他）
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from .messages import Message
from .tools import Tool

DEEPSEEK_BASE = "https://api.deepseek.com/v1"
# 小米 MiMo Token Plan 套餐专属域名（OpenAI 兼容协议）。
# 详见用户截图 / https://platform.xiaomimimo.com/docs/zh-CN/api/chat/openai-api
MIMO_BASE = "https://token-plan-cn.xiaomimimo.com/v1"


class DeepSeekClient:
    """异步流式 DeepSeek API 客户端。

    使用示例::

        client = DeepSeekClient(model="deepseek-v4-flash")
        msgs = [Message.user("你好")]
        async for ev in client.stream(msgs):
            if ev["type"] == "delta":
                print(ev["text"], end="", flush=True)
            elif ev["type"] == "done":
                print()
                break
        await client.aclose()
    """

    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        api_key: str | None = None,
        temperature: float = 0.7,
        api_base: str = DEEPSEEK_BASE,
        timeout: float = 300.0,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY 未设置（env 或构造参数）")
        self.temperature = temperature
        self.api_base = api_base
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        """释放 httpx 连接池。长期运行的 server 在 shutdown 时调用。"""
        await self._client.aclose()

    async def stream(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式调用 DeepSeek，yield 一系列事件 dict。

        Args:
            messages: 完整对话历史（包括 system / user / assistant / tool）。
              每条消息通过 ``Message.to_openai()`` 序列化；其中 assistant 消息
              的 ``reasoning_content`` 字段会**自动随之回传**，避免 thinking
              模式下 400 错误。
            tools: 可选工具列表。提供时启用 tool calling。

        Yields:
            事件 dict，五种 type 见模块文档。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = self._tools_to_payload(tools)

        try:
            async with self._client.stream(
                "POST", f"{self.api_base}/chat/completions", json=payload
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield {
                        "type": "error",
                        "error": f"{resp.status_code} {resp.reason_phrase}: "
                                 f"{body.decode('utf-8', errors='replace')[:500]}",
                    }
                    return

                # tool_calls 在流中是按 index 分片下发的，累积器：
                # {index: {"id": str, "name": str, "arguments": str-json}}
                tool_buffers: dict[int, dict[str, str]] = {}
                reasoning_full: list[str] = []

                async for raw_line in resp.aiter_lines():
                    if not raw_line.startswith("data: "):
                        continue
                    data_str = raw_line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    # 1. 普通文字
                    if (text := delta.get("content")):
                        yield {"type": "delta", "text": text}

                    # 2. reasoning_content（thinking 模式）
                    if (r_text := delta.get("reasoning_content")):
                        reasoning_full.append(r_text)
                        yield {"type": "reasoning", "text": r_text}

                    # 3. tool_calls 分片
                    for tc_delta in (delta.get("tool_calls") or []):
                        idx = tc_delta.get("index", 0)
                        buf = tool_buffers.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc_delta.get("id"):
                            buf["id"] = tc_delta["id"]
                        fn = tc_delta.get("function") or {}
                        if fn.get("name"):
                            buf["name"] += fn["name"]
                        if "arguments" in fn:
                            buf["arguments"] += fn["arguments"] or ""

                    # 4. 完成
                    if (finish := choice.get("finish_reason")):
                        # 把累积的 tool_calls 解析后 yield
                        for idx in sorted(tool_buffers.keys()):
                            buf = tool_buffers[idx]
                            try:
                                args = json.loads(buf["arguments"] or "{}")
                            except json.JSONDecodeError:
                                args = {"_raw": buf["arguments"]}
                            yield {
                                "type": "tool_call",
                                "id": buf["id"],
                                "name": buf["name"],
                                "arguments": args,
                            }
                        yield {
                            "type": "done",
                            "finish_reason": finish,
                            "reasoning_full": "".join(reasoning_full) or None,
                        }
                        return

        except httpx.HTTPError as e:
            yield {"type": "error", "error": f"HTTPError: {e}"}
        except Exception as e:  # noqa: BLE001
            yield {"type": "error", "error": f"Unexpected: {type(e).__name__}: {e}"}

    @staticmethod
    def _tools_to_payload(tools: list[Tool]) -> list[dict[str, Any]]:
        """把 Tool 元数据转成 OpenAI tool-calling 协议要求的 schema。"""
        return [
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


class MiMoClient(DeepSeekClient):
    """小米 MiMo API 客户端（OpenAI 兼容，支持视觉 / 音频 / 视频多模态）。

    与 DeepSeekClient 协议几乎完全一致 ——
    - 同样的 chat/completions 端点结构
    - 同样的 stream 协议
    - 同样的 tool_calls 协议
    所以直接继承，只是默认 base_url 和 api_key 环境变量不同。

    支持的多模态模型（截至 2026-05）：
    - ``mimo-v2.5``      视觉 + 文本
    - ``mimo-v2-omni``   视觉 + 音频 + 文本
    - ``mimo-v2.5-pro``  仅文本（旗舰对话）

    用法::

        client = MiMoClient(model="mimo-v2.5")
        # 多模态消息：content 是 list[dict]
        from .messages import Message
        msg = Message(role="user", content=[
            {"type": "text", "text": "描述这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
        ])
        async for ev in client.stream([msg]):
            ...
    """

    def __init__(
        self,
        model: str = "mimo-v2.5",
        api_key: str | None = None,
        temperature: float = 0.7,
        api_base: str = MIMO_BASE,
        timeout: float = 300.0,
    ) -> None:
        # 不调 super().__init__（它会用 DEEPSEEK_API_KEY 检查），而是自己复现一份
        self.model = model
        self.api_key = api_key or os.getenv("MIMO_API_KEY")
        if not self.api_key:
            raise ValueError("MIMO_API_KEY 未设置（env 或构造参数）")
        self.temperature = temperature
        self.api_base = api_base
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
