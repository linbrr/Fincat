"""TopicStore — unified topic buffer with dedup, expiry, and delivery tracking.

UnifiedTopics are the common currency produced by both ProactiveSkill (rule engine)
and Cron Skills (LLM engine), then dispatched to all enabled channels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# UnifiedTopic
# ---------------------------------------------------------------------------

@dataclass
class UnifiedTopic:
    """A single topic ready for multi-channel dispatch."""

    topic_id: str
    source: str              # "proactive" | "skill" | "event"
    source_name: str         # "deadline" | "high_frequency" | "market-alert" | ...
    category: str            # "alert" | "news" | "reminder" | "insight"
    title: str
    content: str
    priority: int            # 0-3 (3 = urgent)
    entities: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    delivered: dict[str, datetime] = field(default_factory=dict)  # channel -> delivered_at
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "source": self.source,
            "source_name": self.source_name,
            "category": self.category,
            "title": self.title,
            "content": self.content,
            "priority": self.priority,
            "entities": self.entities,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "delivered": {ch: dt.isoformat() for ch, dt in self.delivered.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UnifiedTopic:
        delivered = {}
        for ch, dt_str in d.get("delivered", {}).items():
            try:
                delivered[ch] = datetime.fromisoformat(dt_str)
            except (ValueError, TypeError):
                pass
        expires = d.get("expires_at")
        return cls(
            topic_id=d["topic_id"],
            source=d.get("source", "proactive"),
            source_name=d.get("source_name", ""),
            category=d.get("category", "alert"),
            title=d.get("title", ""),
            content=d.get("content", ""),
            priority=d.get("priority", 1),
            entities=d.get("entities", []),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now(timezone.utc),
            expires_at=datetime.fromisoformat(expires) if expires else None,
            delivered=delivered,
            metadata=d.get("metadata", {}),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            return now.replace(tzinfo=timezone.utc) >= self.expires_at.replace(tzinfo=timezone.utc)
        return now >= self.expires_at

    def is_delivered_to(self, channel: str) -> bool:
        return channel in self.delivered


# ---------------------------------------------------------------------------
# Expiry defaults by category
# ---------------------------------------------------------------------------

_CATEGORY_EXPIRY = {
    "alert": timedelta(hours=24),
    "news": timedelta(hours=24),
    "reminder": None,  # set dynamically from metadata
    "insight": timedelta(days=7),
}


def compute_expires_at(category: str, metadata: dict[str, Any] | None = None) -> datetime | None:
    """Compute default expiry based on category."""
    now = datetime.now(timezone.utc)
    if category == "reminder" and metadata and metadata.get("deadline"):
        try:
            return datetime.fromisoformat(metadata["deadline"])
        except (ValueError, TypeError):
            pass
    delta = _CATEGORY_EXPIRY.get(category)
    return now + delta if delta else None


# ---------------------------------------------------------------------------
# TopicStore
# ---------------------------------------------------------------------------

class TopicStore:
    """Append-only JSONL store for UnifiedTopics with dedup and expiry."""

    _DEDUP_WINDOW = timedelta(hours=1)
    _SIMILARITY_THRESHOLD = 0.7
    _FEEDBACK_DELTAS = {"click": 0.05, "ignore": -0.02, "reject": -0.10, "engage": 0.10}
    _MIN_CONFIDENCE = 0.3

    def __init__(self, path: Path, max_topics: int = 50):
        self._path = Path(path)
        self._max_topics = max_topics
        self._topics: dict[str, UnifiedTopic] = {}
        self._load()

    # -- I/O -----------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        topic = UnifiedTopic.from_dict(json.loads(line))
                        self._topics[topic.topic_id] = topic
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            logger.warning("Failed to load TopicStore from {}", self._path)

    def _append(self, topic: UnifiedTopic) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(topic.to_dict(), ensure_ascii=False) + "\n")
        self._topics[topic.topic_id] = topic

    def _rewrite(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        topics = sorted(self._topics.values(), key=lambda t: t.created_at)
        with open(self._path, "w", encoding="utf-8") as f:
            for topic in topics:
                f.write(json.dumps(topic.to_dict(), ensure_ascii=False) + "\n")

    # -- CRUD ----------------------------------------------------------------

    def add(self, topic: UnifiedTopic) -> bool:
        """Add a topic after dedup check. Returns True if added."""
        if self._is_duplicate(topic):
            logger.debug("TopicStore: dedup skipped '{}'", topic.title[:40])
            return False
        if topic.expires_at is None:
            topic.expires_at = compute_expires_at(topic.category, topic.metadata)
        self._append(topic)
        self._evict()
        logger.debug("TopicStore: added '{}' (priority={}, source={})", topic.title[:40], topic.priority, topic.source)
        return True

    def get_pending(self, channel: str) -> list[UnifiedTopic]:
        """Get topics not yet delivered to *channel*, sorted by priority desc."""
        now = datetime.now(timezone.utc)
        pending = [
            t for t in self._topics.values()
            if not t.is_delivered_to(channel) and not t.is_expired(now)
        ]
        pending.sort(key=lambda t: (-t.priority, t.created_at))
        return pending

    def mark_delivered(self, topic_id: str, channel: str) -> None:
        topic = self._topics.get(topic_id)
        if topic:
            topic.delivered[channel] = datetime.now(timezone.utc)
            self._rewrite()

    def cleanup(self) -> int:
        """Remove expired topics. Returns count removed."""
        now = datetime.now(timezone.utc)
        expired_ids = [tid for tid, t in self._topics.items() if t.is_expired(now)]
        for tid in expired_ids:
            del self._topics[tid]
        if expired_ids:
            self._rewrite()
            logger.info("TopicStore: cleaned up {} expired topic(s)", len(expired_ids))
        return len(expired_ids)

    def remove_topic(self, topic_id: str) -> None:
        """Remove a topic by ID (user feedback: dismiss/reject)."""
        if topic_id in self._topics:
            del self._topics[topic_id]
            self._rewrite()
            logger.debug("TopicStore: removed topic {} (user feedback)", topic_id)

    def adjust_confidence(self, topic_id: str, feedback: str) -> float:
        """Adjust topic confidence based on user feedback. Returns new confidence."""
        delta = self._FEEDBACK_DELTAS.get(feedback, 0.0)
        topic = self._topics.get(topic_id)
        if not topic:
            return 0.5
        old = topic.metadata.get("confidence", 0.5)
        new_conf = max(0.1, min(1.0, old + delta))
        topic.metadata["confidence"] = new_conf
        self._rewrite()
        return new_conf

    def on_user_feedback(self, topic_id: str, feedback: str) -> None:
        """Handle user feedback: adjust confidence + remove if rejected."""
        new_conf = self.adjust_confidence(topic_id, feedback)
        if feedback == "reject":
            self.remove_topic(topic_id)
        elif new_conf < self._MIN_CONFIDENCE:
            self.remove_topic(topic_id)
            logger.info("TopicStore: removed low-confidence topic {}", topic_id)

    def get_all(self) -> list[UnifiedTopic]:
        return list(self._topics.values())

    def __len__(self) -> int:
        return len(self._topics)

    # -- Dedup ---------------------------------------------------------------

    def _evict(self) -> None:
        """Evict lowest-priority + oldest topics when exceeding max_topics."""
        if len(self._topics) <= self._max_topics:
            return
        sorted_topics = sorted(
            self._topics.values(), key=lambda t: (t.priority, t.created_at)
        )
        to_remove = len(self._topics) - self._max_topics
        for t in sorted_topics[:to_remove]:
            del self._topics[t.topic_id]
        self._rewrite()
        logger.debug("TopicStore: evicted {} low-priority topics", to_remove)

    def _is_duplicate(self, new_topic: UnifiedTopic) -> bool:
        now = datetime.now(timezone.utc)
        for existing in self._topics.values():
            if existing.source_name != new_topic.source_name:
                continue
            elapsed = abs((now - existing.created_at).total_seconds())
            if elapsed > self._DEDUP_WINDOW.total_seconds():
                continue
            if self._token_similarity(existing.title, new_topic.title) >= self._SIMILARITY_THRESHOLD:
                return True
        return False

    @staticmethod
    def _token_similarity(a: str, b: str) -> float:
        def _tokens(s: str) -> set[str]:
            parts = set(s.split())
            chars = "".join(ch for ch in s if "一" <= ch <= "鿿")
            for i in range(len(chars) - 1):
                parts.add(chars[i : i + 2])
            return parts
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / min(len(ta), len(tb))
