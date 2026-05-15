"""条件性 @observe 装饰器 — langfuse 未配置时为 no-op。

Langfuse SDK v4.x 使用 OpenTelemetry，LLM 调用通过 langfuse.openai.AsyncOpenAI
自动追踪。此装饰器保留为 identity decorator，确保代码导入不出错。
"""

from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable


def observe(
    func: Callable | None = None,
    *,
    name: str | None = None,
    capture_input: bool = True,
    capture_output: bool = True,
) -> Any:
    """条件性 Langfuse @observe 装饰器。

    Langfuse SDK v4.x 不再提供 langfuse.decorators.observe。
    LLM 调用通过 langfuse.openai.AsyncOpenAI 自动追踪。
    此装饰器保留为 identity decorator，确保导入兼容。
    """
    if func is None:
        return lambda f: f
    return func
