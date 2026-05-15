"""MemoryRetrievalFilter — pre-filter before memory retrieval.

Decides whether a user message needs memory retrieval at all.
Three layers of filtering, all pure CPU (0 token, 0 vector ops):

1. Intent classification: skip non-memory queries (weather, time, search, etc.)
2. Keyword blacklist: skip requests containing certain keywords
3. Session context cache: avoid redundant retrieval within the same session
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# Intent patterns — requests that NEVER need memory retrieval
# ---------------------------------------------------------------------------

_SKIP_INTENT_PATTERNS: list[re.Pattern] = [
    # Real-time data queries
    re.compile(r"(今天|现在|当前|实时|最新).{0,10}(天气|温度|股价|金价|汇率|指数|行情)", re.I),
    re.compile(r"(查一下|帮我查|搜一下|帮我搜|搜索).{0,15}(天气|股价|金价|新闻|最新)", re.I),
    re.compile(r"(天气|温度|下雨).{0,5}(怎么样|如何|吗)", re.I),
    # Simple tool requests
    re.compile(r"^(帮我搜|搜索|查一下|帮我查)\s*\S{2,20}$"),
    re.compile(r"^(打开|启动|关闭|停止)\s*\S{2,10}$"),
    # Time/date queries
    re.compile(r"(现在几点|今天(星期|周)几|今天(是)?(什么|哪)(日子|天|日期))"),
    # Greetings / short social
    re.compile(r"^(你好|hi|hello|hey|嗨|哈喽|在吗|在不在)\s*[！!？?。.]*$", re.I),
    # Math / calculation
    re.compile(r"^\s*\d+[\s]*[+\-×÷*/]\s*\d+"),
    # System commands
    re.compile(r"^/(help|clear|reset|dream|status|quit|exit)\s*$", re.I),
]

# ---------------------------------------------------------------------------
# Keyword blacklist — skip memory retrieval when these appear
# ---------------------------------------------------------------------------

_DEFAULT_BLACKLIST: list[str] = [
    "帮我搜下", "帮我搜索", "实时数据", "实时行情", "最新消息",
    "当前价格", "现在价格", "今天天气", "帮我查一下",
    "百度一下", "谷歌一下", "网上搜",
]

# ---------------------------------------------------------------------------
# Session cache config
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE_MAX_SESSIONS = 100


@dataclass
class _CacheEntry:
    """One cached memory retrieval result."""
    retrieval_ts: float
    memory_context: str
    item_ids: list[str] = field(default_factory=list)


class MemoryRetrievalFilter:
    """Pre-filter that decides whether a message needs memory retrieval.

    Three-layer filtering (all pure CPU, 0 cost):
    1. Intent classification — skip non-memory requests entirely
    2. Keyword blacklist — skip requests with blacklisted keywords
    3. Session context cache — reuse recent retrieval results

    Usage:
        filter = MemoryRetrievalFilter()
        decision = filter.should_retrieve("今天天气怎么样", session_key="s1")
        if decision.skip:
            # No memory retrieval needed
            pass
        else:
            # Proceed with vector search
            memory = retrieve_memory(...)
            filter.cache_result(session_key, memory_context, item_ids)
    """

    def __init__(
        self,
        blacklist: list[str] | None = None,
        cache_ttl: int = _CACHE_TTL_SECONDS,
    ):
        self._blacklist = set(blacklist or _DEFAULT_BLACKLIST)
        self._cache_ttl = cache_ttl
        self._session_cache: dict[str, _CacheEntry] = {}
        self._skip_count = 0
        self._total_count = 0

    def should_retrieve(
        self,
        user_message: str,
        session_key: str = "",
        *,
        tools_used: list[str] | None = None,
    ) -> RetrievalDecision:
        """Decide whether to retrieve memory for this message.

        Returns RetrievalDecision with skip flag and optional cached context.
        """
        self._total_count += 1

        # Layer 1: Intent classification
        if self._is_skip_intent(user_message):
            self._skip_count += 1
            logger.debug("MemoryRetrievalFilter: skip (intent) msg={}", user_message[:40])
            return RetrievalDecision(skip=True, reason="intent")

        # Layer 2: Keyword blacklist
        if self._matches_blacklist(user_message):
            self._skip_count += 1
            logger.debug("MemoryRetrievalFilter: skip (blacklist) msg={}", user_message[:40])
            return RetrievalDecision(skip=True, reason="blacklist")

        # Layer 3: Session context cache
        if session_key:
            cached = self._get_cached(session_key)
            if cached is not None:
                self._skip_count += 1
                logger.debug("MemoryRetrievalFilter: skip (cache) session={}", session_key)
                return RetrievalDecision(
                    skip=False,  # Still process, but use cached context
                    reason="cache_hit",
                    cached_context=cached.memory_context,
                    cached_item_ids=cached.item_ids,
                )

        return RetrievalDecision(skip=False, reason="full_retrieval")

    def cache_result(
        self,
        session_key: str,
        memory_context: str,
        item_ids: list[str] | None = None,
    ) -> None:
        """Cache the memory retrieval result for a session."""
        if not session_key:
            return
        # Evict oldest entries if cache is full
        if len(self._session_cache) >= _CACHE_MAX_SESSIONS:
            oldest_key = min(
                self._session_cache,
                key=lambda k: self._session_cache[k].retrieval_ts,
            )
            del self._session_cache[oldest_key]

        self._session_cache[session_key] = _CacheEntry(
            retrieval_ts=time.time(),
            memory_context=memory_context,
            item_ids=item_ids or [],
        )

    def invalidate_session(self, session_key: str) -> None:
        """Remove a session from the cache."""
        self._session_cache.pop(session_key, None)

    def clear_cache(self) -> None:
        """Clear all cached sessions."""
        self._session_cache.clear()

    @property
    def stats(self) -> dict[str, Any]:
        """Return filter statistics."""
        return {
            "total": self._total_count,
            "skipped": self._skip_count,
            "skip_rate": self._skip_count / max(1, self._total_count),
            "cached_sessions": len(self._session_cache),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_skip_intent(self, text: str) -> bool:
        """Check if the message matches any skip-intent pattern."""
        text = text.strip()
        if len(text) < 3:
            return True  # Very short messages don't need memory
        for pattern in _SKIP_INTENT_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def _matches_blacklist(self, text: str) -> bool:
        """Check if the message contains any blacklisted keyword."""
        for kw in self._blacklist:
            if kw in text:
                return True
        return False

    def _get_cached(self, session_key: str) -> _CacheEntry | None:
        """Get cached result if still valid."""
        entry = self._session_cache.get(session_key)
        if entry is None:
            return None
        if time.time() - entry.retrieval_ts > self._cache_ttl:
            del self._session_cache[session_key]
            return None
        return entry


@dataclass
class RetrievalDecision:
    """Result of the retrieval pre-filter check."""
    skip: bool
    reason: str  # "intent", "blacklist", "cache_hit", "full_retrieval"
    cached_context: str = ""
    cached_item_ids: list[str] = field(default_factory=list)
