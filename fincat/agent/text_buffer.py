"""RecentItemBuffer — in-memory full-text buffer for recent un-indexed items.

Catches items that haven't been vectorized yet (same-day entries) using
keyword/substring matching as a fallback layer before vector search.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class _BufferItem:
    item_id: str
    content: str
    category_id: str | None = None
    created_at: float = field(default_factory=time.time)


class RecentItemBuffer:
    """In-memory buffer for items not yet vectorized.

    Provides keyword/substring matching as a supplement to vector search.
    Items older than max_age_days are automatically evicted.
    """

    def __init__(self, max_items: int = 500, max_age_days: int = 7):
        self._max_items = max_items
        self._max_age = max_age_days * 86400  # seconds
        self._items: OrderedDict[str, _BufferItem] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, item_id: str, content: str, category_id: str | None = None) -> None:
        """Add an item to the buffer."""
        with self._lock:
            if item_id in self._items:
                self._items.move_to_end(item_id)
                self._items[item_id].content = content
                self._items[item_id].category_id = category_id
                return
            if len(self._items) >= self._max_items:
                self._items.popitem(last=False)
            self._items[item_id] = _BufferItem(
                item_id=item_id,
                content=content,
                category_id=category_id,
            )

    def search(self, query: str, top_k: int = 3, category_ids: list[str] | None = None) -> list[dict]:
        """Keyword/substring search over buffered items.

        Returns:
            List of dicts: {item_id, content, category_id, score}
        """
        if not query or not query.strip():
            return []

        query_lower = query.strip().lower()
        keywords = [kw for kw in query_lower.split() if len(kw) >= 2]

        results = []
        with self._lock:
            self._evict_stale()
            for item in self._items.values():
                if category_ids and item.category_id and item.category_id not in category_ids:
                    continue
                content_lower = item.content.lower()
                score = self._compute_score(content_lower, query_lower, keywords)
                if score > 0:
                    results.append({
                        "item_id": item.item_id,
                        "content": item.content,
                        "category_id": item.category_id,
                        "score": score,
                        "source": "text_buffer",
                    })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def remove(self, item_id: str) -> bool:
        """Remove a specific item. Returns True if found."""
        with self._lock:
            if item_id in self._items:
                del self._items[item_id]
                return True
            return False

    def clear(self) -> int:
        """Clear all buffered items. Returns count removed."""
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def evict_stale(self) -> int:
        """Manually trigger eviction of stale items. Returns count removed."""
        with self._lock:
            return self._evict_stale()

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._items),
                "max_items": self._max_items,
                "max_age_days": self._max_age / 86400,
            }

    def _evict_stale(self) -> int:
        """Remove items older than max_age. Must hold self._lock."""
        now = time.time()
        stale_keys = [k for k, v in self._items.items() if now - v.created_at > self._max_age]
        for k in stale_keys:
            del self._items[k]
        return len(stale_keys)

    @staticmethod
    def _compute_score(content: str, query: str, keywords: list[str]) -> float:
        """Compute a simple relevance score (0.0 to 1.0)."""
        # Exact substring match — highest score
        if query in content:
            # Shorter content with the match gets higher score
            return min(1.0, len(query) / len(content) + 0.5)

        if not keywords:
            return 0.0

        # Keyword coverage
        matched = sum(1 for kw in keywords if kw in content)
        if matched == 0:
            return 0.0
        return matched / len(keywords) * 0.7
