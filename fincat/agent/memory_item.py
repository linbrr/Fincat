"""MemoryItem — atomic memory units with decay, frequency, and confidence tracking.

MemoryItems are the data layer (structured, queryable, monitorable).
Category Markdown files are the expression layer (human-readable, injectable).
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HALF_LIFE_DAYS = 30  # decay half-life
DECAY_LAMBDA = math.log(2) / HALF_LIFE_DAYS

CATEGORIES = ["preference", "knowledge", "case", "compliance"]

# Confidence ramp
CONFIDENCE_TABLE = {1: 0.5, 2: 0.6, 3: 0.7, 5: 0.9}


@dataclass
class MemoryItem:
    """Atomic, self-contained memory unit with temporal metadata."""

    item_id: str
    timestamp: datetime
    content: str
    category: str
    source_session: str
    source_round: int = 0

    # Frequency & decay
    frequency: int = 0
    last_accessed: datetime | None = None
    decay_score: float = 1.0

    # Confidence grows with repeated observations
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "timestamp": self.timestamp.isoformat(),
            "content": self.content,
            "category": self.category,
            "source_session": self.source_session,
            "source_round": self.source_round,
            "frequency": self.frequency,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
            "decay_score": round(self.decay_score, 4),
            "confidence": round(self.confidence, 2),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryItem:
        la = d.get("last_accessed")
        return cls(
            item_id=d["item_id"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            content=d["content"],
            category=d.get("category", "knowledge"),
            source_session=d.get("source_session", ""),
            source_round=d.get("source_round", 0),
            frequency=d.get("frequency", 0),
            last_accessed=datetime.fromisoformat(la) if la else None,
            decay_score=d.get("decay_score", 1.0),
            confidence=d.get("confidence", 0.5),
        )

    # ------------------------------------------------------------------
    # Decay
    # ------------------------------------------------------------------

    def compute_decay(self, *, now: datetime | None = None) -> float:
        """Recompute decay_score based on days since last access."""
        now = now or datetime.now(timezone.utc)
        ref = self.last_accessed or self.timestamp
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        days = (now - ref).total_seconds() / 86400.0
        self.decay_score = self.confidence * math.exp(-DECAY_LAMBDA * max(days, 0))
        return self.decay_score

    def touch(self, *, now: datetime | None = None) -> None:
        """Record an access, bump frequency, and recompute decay."""
        self.last_accessed = now or datetime.now(timezone.utc)
        self.frequency += 1
        self.compute_decay(now=now)

    @staticmethod
    def confidence_for_observations(n: int) -> float:
        """Map observation count to confidence score."""
        thresholds = sorted(CONFIDENCE_TABLE.keys(), reverse=True)
        for t in thresholds:
            if n >= t:
                return CONFIDENCE_TABLE[t]
        return 0.5


# ---------------------------------------------------------------------------
# MemoryItemStore — append-only JSONL + query
# ---------------------------------------------------------------------------


class MemoryItemStore:
    """Append-only JSONL store for MemoryItems with dedup merging."""

    _SIMILARITY_THRESHOLD = 0.8

    def __init__(self, path: Path):
        self.path = Path(path)
        self._items: dict[str, MemoryItem] = {}
        self._load()

    # -- I/O ---------------------------------------------------------------

    def _load(self) -> None:
        """Load all items from the JSONL file."""
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = MemoryItem.from_dict(json.loads(line))
                        self._items[item.item_id] = item
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            logger.warning("Failed to load MemoryItem store from {}", self.path)

    def _append(self, item: MemoryItem) -> None:
        """Append a single item to the JSONL file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        self._items[item.item_id] = item

    def _rewrite(self) -> None:
        """Rewrite the entire JSONL file from in-memory items."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        items = sorted(self._items.values(), key=lambda it: it.timestamp)
        for item in items:
            item.compute_decay()  # ensure decay_score is fresh before writing
        with open(self.path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")

    # -- CRUD --------------------------------------------------------------

    def add(
        self,
        content: str,
        category: str,
        *,
        source_session: str = "",
        source_round: int = 0,
        timestamp: datetime | None = None,
    ) -> MemoryItem:
        """Add a new MemoryItem, merging if a similar one exists.

        Returns the new or merged item.
        """
        ts = timestamp or datetime.now(timezone.utc)
        existing = self._find_similar(content, category)
        if existing:
            existing.frequency += 1
            existing.confidence = MemoryItem.confidence_for_observations(existing.frequency)
            existing.last_accessed = ts
            existing.compute_decay(now=ts)
            self._rewrite()
            logger.debug(
                "MemoryItem merged: id={} freq={} conf={:.2f}",
                existing.item_id, existing.frequency, existing.confidence,
            )
            return existing

        item = MemoryItem(
            item_id=str(uuid.uuid4())[:8],
            timestamp=ts,
            content=content,
            category=category,
            source_session=source_session,
            source_round=source_round,
            frequency=1,
            last_accessed=ts,
            decay_score=0.5,  # initial confidence 0.5
            confidence=0.5,
        )
        self._append(item)
        return item

    def get(self, item_id: str) -> MemoryItem | None:
        item = self._items.get(item_id)
        if item:
            item.touch()
        return item

    def query(
        self,
        *,
        category: str | None = None,
        min_decay: float | None = None,
        min_frequency: int = 0,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """Query items with optional filters, sorted by decay_score descending."""
        results = []
        now = datetime.now(timezone.utc)
        for item in self._items.values():
            if category and item.category != category:
                continue
            item.compute_decay(now=now)
            if min_decay is not None and item.decay_score < min_decay:
                continue
            if item.frequency < min_frequency:
                continue
            if since and item.timestamp < since:
                continue
            results.append(item)
        results.sort(key=lambda it: it.decay_score, reverse=True)
        return results[:limit]

    def all(self) -> list[MemoryItem]:
        return list(self._items.values())

    def remove(self, item_id: str) -> bool:
        if item_id in self._items:
            del self._items[item_id]
            self._rewrite()
            return True
        return False

    def archive(self, item_id: str) -> bool:
        """Move a low-decay item to archive.jsonl and remove from active store."""
        item = self._items.get(item_id)
        if not item:
            return False
        archive_path = self.path.parent / "archive.jsonl"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(archive_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to archive item {}", item_id)
            return False
        del self._items[item_id]
        self._rewrite()
        logger.info("Archived MemoryItem: id={} decay={:.2f}", item_id, item.decay_score)
        return True

    # -- dedup -------------------------------------------------------------

    def _find_similar(self, content: str, category: str) -> MemoryItem | None:
        """Find an existing item in the same category with high content similarity."""
        best_score = 0.0
        best_item: MemoryItem | None = None
        for item in self._items.values():
            if item.category != category:
                continue
            score = self._token_similarity(content, item.content)
            if score > best_score:
                best_score = score
                best_item = item
        return best_item if best_score >= self._SIMILARITY_THRESHOLD else None

    @staticmethod
    def _token_similarity(a: str, b: str) -> float:
        """Simple token-overlap similarity for Chinese + English text."""
        def _tokens(s: str) -> set[str]:
            # Split on whitespace and also extract 2-char Chinese bigrams
            parts = set(s.split())
            # Add character bigrams for CJK text
            chars = "".join(ch for ch in s if "一" <= ch <= "鿿")
            for i in range(len(chars) - 1):
                parts.add(chars[i : i + 2])
            return parts

        ta = _tokens(a)
        tb = _tokens(b)
        if not ta or not tb:
            return 0.0
        intersection = ta & tb
        return len(intersection) / min(len(ta), len(tb))

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# Factory — create store bound to memory directory
# ---------------------------------------------------------------------------


def create_item_store(memory_dir: Path) -> MemoryItemStore:
    return MemoryItemStore(memory_dir / "items.jsonl")
