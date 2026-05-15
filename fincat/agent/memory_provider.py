"""Memory provider interface — plugin point for prefetch and memory write hooks.

Reference: hermes-agent/agent/memory_provider.py
"""

from abc import ABC, abstractmethod


class MemoryProvider(ABC):
    """Abstract base class for memory providers.

    Implement `prefetch()` to add context to every LLM call.
    Implement `on_memory_write()` to be notified when skills/memory are created/updated.
    """

    @property
    def name(self) -> str:
        """Provider name, used to skip 'builtin' in on_memory_write broadcasts."""
        return self.__class__.__name__

    @abstractmethod
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return context string relevant to the query.

        Called once per user turn, before the agent loop starts.
        Return empty string if nothing relevant.
        """
        ...

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Called when the built-in skill/memory tool writes an entry.

        action: 'create', 'edit', 'patch', 'delete'
        target: 'skill' or 'memory'
        content: the skill name or memory entry content

        Use to mirror writes to your backend or update internal state.
        Default no-op.
        """
        pass
