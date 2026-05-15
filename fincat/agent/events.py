"""Internal event system for decoupled async event publish/subscribe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable


@dataclass
class TaskReflectionEvent:
    """
        在任务完成之后发布，推动自动skill生成。
    This event carries the full task context needed by TaskReflectionGenerator
    to produce a structured reflection, which then feeds into the SkillEvolver
    for deduplication, improvement, and validation.
    """
    timestamp: str  # ISO 8601 string (datetime.now().isoformat())
    user_message: str
    assistant_response: str  # First 500 chars of the assistant's response
    tools_used: list[str]   # List of tool names called
    iterations: int         # Number of LLM rounds
    success_score: float    # Success score 0.0-1.0 (1.0 = perfect)


class EventBus:
    """Simple async event bus for internal agent events."""

    def __init__(self):
        self._handlers: dict[type, list[Callable[[Any], Awaitable[None]]]] = {}

    def subscribe(self, event_type: type, handler: Callable[[Any], Awaitable[None]]) -> None:
        """Subscribe a handler to an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler: Callable[[Any], Awaitable[None]]) -> None:
        """Unsubscribe a handler from an event type."""
        if event_type in self._handlers:
            self._handlers[event_type] = [h for h in self._handlers[event_type] if h != handler]

    async def publish(self, event: Any) -> None:
        """Publish an event to all subscribed handlers."""
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                import traceback
                traceback.print_exc()
