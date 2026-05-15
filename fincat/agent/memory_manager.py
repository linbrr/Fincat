"""MemoryManager — orchestrates multiple MemoryProvider plugins.

Reference: hermes-agent/agent/memory_manager.py

Usage:
    mm = MemoryManager([SkillMemoryProvider(workspace), ...])
    context = mm.prefetch_all(user_message)      # called each turn
    mm.on_memory_write("create", "skill", "stock-analysis")  # called after skill writes
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from fincat.agent.memory_provider import MemoryProvider

logger.bind(name="memory-manager")


class MemoryManager:
    """Manages memory providers and delegates prefetch / on_memory_write calls."""

    def __init__(self, providers: list[MemoryProvider] | None = None):
        self._providers: list[MemoryProvider] = providers or []

    # -- registration --------------------------------------------------------

    def register_provider(self, provider: MemoryProvider) -> None:
        """Add a provider to the manager."""
        self._providers.append(provider)
        logger.debug("Registered memory provider: {}", provider.name)

    @property
    def providers(self) -> list[MemoryProvider]:
        """All registered providers (read-only view)."""
        return list(self._providers)

    # -- prefetch ------------------------------------------------------------

    def prefetch_all(self, query: str, *, session_id: str = "") -> str:
        """Collect prefetch context from all providers.

        Returns merged context text labeled by provider.
        Failures in one provider do not block others.
        """
        if not self._providers:
            return ""

        parts: list[str] = []
        for provider in self._providers:
            try:
                result = provider.prefetch(query, session_id=session_id)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug(
                    "Memory provider '{}' prefetch failed (non-fatal): {}",
                    provider.name, e,
                )
        return "\n\n".join(parts)

    # -- memory write notification -------------------------------------------

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Notify all external providers when a skill/memory write occurs.

        Skips the 'builtin' provider (it is the source of the write).
        """
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            try:
                provider.on_memory_write(action, target, content)
            except Exception as e:
                logger.debug(
                    "Memory provider '{}' on_memory_write failed (non-fatal): {}",
                    provider.name, e,
                )
