"""LangfuseHook — AgentHook 实现，将 Agent 循环映射到 Langfuse trace/span。

使用 Langfuse SDK v4.x API（基于 OpenTelemetry）：
- 整个 run() 调用 = 1 个 trace（通过 trace_context 关联）
- 每次迭代 = 1 个 span（使用 start_as_current_observation 建立 OTEL 上下文）
- 每个工具调用 = 1 个 tool span（子级，自动继承父上下文）
"""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from typing import Any

from loguru import logger

from fincat.agent.hook import AgentHook, AgentHookContext


class LangfuseHook(AgentHook):
    """将 AgentRunner ReAct 循环自动上报到 Langfuse。

    环境变量控制：
    - LANGFUSE_SECRET_KEY + LANGFUSE_PUBLIC_KEY：连接凭证
    - FINCAT_EVAL_ENABLED=1：启用评测模式（记录更多元数据）
    """

    def __init__(
        self,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        channel: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._session_id = session_id
        self._user_id = user_id
        self._channel = channel
        self._metadata = metadata or {}
        self._trace_context: dict[str, Any] | None = None
        self._iteration_cm: AbstractContextManager | None = None
        self._iteration_span: Any = None
        self._tool_cms: dict[str, AbstractContextManager] = {}
        self._tool_spans: dict[str, Any] = {}
        self._start_time: float | None = None
        self._langfuse = _get_langfuse_client()

    def _ensure_trace(self) -> None:
        if self._trace_context is not None or self._langfuse is None:
            return
        self._trace_context = {
            "session_id": self._session_id,
            "user_id": self._user_id,
            "metadata": {"channel": self._channel, **self._metadata},
        }
        self._start_time = time.time()

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._ensure_trace()
        if self._langfuse is None or self._trace_context is None:
            return
        cm = self._langfuse.start_as_current_observation(
            trace_context=self._trace_context,
            name=f"iteration_{context.iteration}",
            as_type="span",
            input={"messages": _messages_to_langfuse_input(context.messages)},
            metadata={"iteration": context.iteration},
        )
        self._iteration_span = cm.__enter__()
        self._iteration_cm = cm

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._iteration_span is None:
            return
        for tc in context.tool_calls:
            cm = self._iteration_span.start_as_current_observation(
                name=tc.name,
                as_type="tool",
                input=tc.arguments,
            )
            span = cm.__enter__()
            self._tool_cms[tc.id] = cm
            self._tool_spans[tc.id] = span

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._iteration_span is None:
            return

        # 关闭工具 spans
        for i, tc in enumerate(context.tool_calls):
            tool_span = self._tool_spans.pop(tc.id, None)
            tool_cm = self._tool_cms.pop(tc.id, None)
            if tool_span and tool_cm:
                result = context.tool_results[i] if i < len(context.tool_results) else None
                tool_span.update(output=str(result)[:500] if result else None)
                tool_cm.__exit__(None, None, None)

        # 记录 LLM generation（作为迭代 span 的子级）
        if context.response:
            gen_cm = self._iteration_span.start_as_current_observation(
                name=f"llm_call_{context.iteration}",
                as_type="generation",
                input=_messages_to_langfuse_input(context.messages),
                output=context.response.content,
                model=context.usage.get("model") if context.usage else None,
                usage_details={
                    "input": context.usage.get("prompt_tokens", 0),
                    "output": context.usage.get("completion_tokens", 0),
                } if context.usage else None,
            )
            gen_cm.__enter__()
            gen_cm.__exit__(None, None, None)

        # 关闭迭代 span
        self._iteration_span.update(
            output=context.response.content[:2000] if context.response and context.response.content else None,
            metadata={
                "usage": context.usage,
                "stop_reason": context.stop_reason,
                "error": context.error,
            },
        )
        if self._iteration_cm:
            self._iteration_cm.__exit__(None, None, None)
            self._iteration_cm = None
        self._iteration_span = None

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        if self._langfuse and self._trace_context and content:
            latency_ms = int((time.time() - self._start_time) * 1000) if self._start_time else 0
            self._langfuse.create_event(
                trace_context=self._trace_context,
                name="agent_finalized",
                output=content[:5000],
                metadata={
                    **self._metadata,
                    "latency_ms": latency_ms,
                    "total_iterations": context.iteration + 1,
                    "stop_reason": context.stop_reason or "completed",
                    "tools_used": list({tc.name for tc in context.tool_calls}),
                    "channel": self._channel,
                },
            )
        return content

    def flush(self) -> None:
        if self._langfuse:
            self._langfuse.flush()


def _get_langfuse_client() -> Any:
    """获取全局 Langfuse 客户端（单例）。"""
    from fincat.eval.config import LANGFUSE_ENABLED, LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

    if not LANGFUSE_ENABLED:
        return None
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
    except Exception as e:
        logger.warning("Langfuse 初始化失败: {}", e)
        return None


def _messages_to_langfuse_input(messages: list[dict]) -> list[dict]:
    """将 OpenAI 格式消息转换为 Langfuse input 格式。"""
    result = []
    for msg in messages[-10:]:
        entry: dict[str, Any] = {"role": msg.get("role", "unknown")}
        content = msg.get("content", "")
        if isinstance(content, str):
            entry["content"] = content[:2000]
        elif isinstance(content, list):
            entry["content"] = str(content)[:2000]
        result.append(entry)
    return result
