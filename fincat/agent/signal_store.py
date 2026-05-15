"""SignalStore — unified signal query layer + external event cache.

Read-only aggregation of:
  - MemoryItemStore (items.jsonl)
  - MemoryStore (7 category markdown files)
  - PatternSnapshotStore (.pattern_snapshots.jsonl)
  - ExternalEvent cache (in-memory, 4h TTL)

Does NOT store new data — all writes go to the underlying stores.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from fincat.agent.memory_item import MemoryItem, MemoryItemStore
from fincat.agent.event_sources import ExternalEvent


class SignalStore:
    """Unified read-only query layer over all signal sources."""

    _EVENT_CACHE_TTL = timedelta(hours=4)
    _EVENT_CACHE_MAX = 100

    def __init__(
        self,
        item_store: MemoryItemStore,
        memory: Any,  # MemoryStore
        snapshot_store: Any,  # PatternSnapshotStore
    ):
        self._items = item_store
        self._memory = memory
        self._snapshots = snapshot_store
        self._event_cache: list[ExternalEvent] = []

    # ── Query interfaces (items.jsonl) ──────────────────────────────────────

    def get_active_interests(self, min_decay: float = 0.3) -> list[MemoryItem]:
        """Items with decay_score >= min_decay, sorted by decay desc."""
        return self._items.query(min_decay=min_decay)

    def get_high_frequency_items(self, threshold: int = 3) -> list[MemoryItem]:
        """Items with frequency >= threshold."""
        return self._items.query(min_frequency=threshold)

    def get_deadlines(self, days: int = 7, importance: str | None = None) -> list[MemoryItem]:
        """Deadline-category items within *days* window."""
        now = datetime.now(timezone.utc)
        results = []
        for item in self._items.query(category="deadline"):
            deadline_str = item.metadata.get("deadline") if hasattr(item, "metadata") else None
            # Metadata is not a field on MemoryItem — check via the to_dict roundtrip
            # For now, parse from the item's content or stored dict
            deadline_str = self._extract_deadline(item)
            if not deadline_str:
                continue
            try:
                deadline = datetime.fromisoformat(deadline_str)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                days_left = (deadline - now).total_seconds() / 86400
                if days_left <= days:
                    if importance and self._extract_importance(item) != importance:
                        continue
                    results.append(item)
            except (ValueError, TypeError):
                continue
        return results

    def get_behavior_patterns(self) -> list[MemoryItem]:
        """Items in the behavior category."""
        return self._items.query(category="behavior")

    def get_category_text(self, category: str) -> str:
        """Read raw markdown text for a category."""
        return self._memory.read_category(category) or ""

    def get_entity_trends(self) -> dict[str, Any]:
        """Compare current item distribution against latest snapshot."""
        latest = self._snapshots.load_latest()
        if not latest:
            return {}
        current_items = self._items.all()
        entity_freq: dict[str, int] = {}
        for item in current_items:
            from fincat.agent.pattern_snapshot import extract_entities_from_text
            for entity in extract_entities_from_text(item.content):
                entity_freq[entity] = entity_freq.get(entity, 0) + item.frequency
        sorted_current = sorted(entity_freq.items(), key=lambda x: x[1], reverse=True)[:20]
        current_entities = {name for name, _ in sorted_current}
        new_entities = current_entities - set(latest.top_entities)
        return {
            "current_top": [name for name, _ in sorted_current],
            "new_entities": list(new_entities),
            "snapshot_top": latest.top_entities,
        }

    def get_auto_cron_candidates(self) -> list[MemoryItem]:
        """Stable periodic behavior patterns (high confidence, behavior category)."""
        results = []
        for item in self._items.query(category="behavior"):
            # Stable pattern: confidence >= 0.8 and frequency >= 3
            if item.confidence >= 0.8 and item.frequency >= 3:
                # Check if metadata indicates a periodic pattern
                # This is set by Dream when extracting behavior patterns
                results.append(item)
        return results

    # ── External event cache ────────────────────────────────────────────────

    def add_event(self, event: ExternalEvent) -> None:
        """Cache an external event (4h TTL, max 100)."""
        self._event_cache.append(event)
        self._evict_events()
        logger.debug("SignalStore: cached event '{}' (total={})", event.title[:40], len(self._event_cache))

    def get_recent_events(self, hours: float = 4) -> list[ExternalEvent]:
        """Return events from the last *hours* hours."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)
        return [
            e for e in self._event_cache
            if e.timestamp.replace(tzinfo=timezone.utc) >= cutoff
        ]

    def _evict_events(self) -> None:
        """Remove expired and overflow events."""
        now = datetime.now(timezone.utc)
        cutoff = now - self._EVENT_CACHE_TTL
        self._event_cache = [
            e for e in self._event_cache
            if e.timestamp.replace(tzinfo=timezone.utc) >= cutoff
        ]
        if len(self._event_cache) > self._EVENT_CACHE_MAX:
            self._event_cache = self._event_cache[-self._EVENT_CACHE_MAX:]

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_deadline(item: MemoryItem) -> str | None:
        """Extract deadline from item content or stored dict."""
        # Items stored via Dream may have deadline in content like "[deadline=2026-05-10]"
        import re
        m = re.search(r"deadline[=：]\s*(\d{4}-\d{2}-\d{2})", item.content)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_importance(item: MemoryItem) -> str:
        """Extract importance from item content."""
        import re
        m = re.search(r"importance[=：]\s*(high|medium|low)", item.content)
        if m:
            return m.group(1)
        return "medium"
