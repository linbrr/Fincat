"""TopicCache — Top-N topic management with expiry, confidence adjustment, and push."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_ADJUSTMENTS = {"click": 0.05, "ignore": -0.02, "reject": -0.10}
_MIN_CONFIDENCE = 0.3


class TopicCache:
    """Topic cache: Top-N management, expiry cleanup, confidence adjustment."""

    def __init__(self, cache_path: Path, max_topics: int = 3):
        self._path = cache_path
        self._max_topics = max_topics

    def set_topics(self, topics: list[dict]) -> None:
        """Write Top-N topics sorted by priority (descending)."""
        # Merge with existing, remove expired
        existing = self.get_topics()
        self.clean_expired()
        existing = self.get_topics()

        # Merge: new topics override existing ones with same topic_id
        by_id: dict[str, dict] = {t["topic_id"]: t for t in existing}
        for t in topics:
            tid = t.get("topic_id") or f"topic_{uuid.uuid4().hex[:8]}"
            t["topic_id"] = tid
            by_id[tid] = t

        # Sort by priority descending, take top N
        sorted_topics = sorted(by_id.values(), key=lambda t: t.get("priority", 0), reverse=True)
        top = sorted_topics[: self._max_topics]

        self._write(top)
        logger.debug("TopicCache: set {} topics", len(top))

    def get_topics(self) -> list[dict]:
        """Read current topic list."""
        if not self._path.exists():
            return []
        topics = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    topics.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return topics

    def remove_topic(self, topic_id: str) -> None:
        """Remove a topic by ID (user rejected)."""
        topics = [t for t in self.get_topics() if t["topic_id"] != topic_id]
        self._write(topics)
        logger.debug("TopicCache: removed topic {}", topic_id)

    def clean_expired(self) -> None:
        """Remove topics where expires_at has passed."""
        now = datetime.now(timezone.utc).isoformat()
        topics = self.get_topics()
        valid = []
        for t in topics:
            expires = t.get("expires_at", "")
            if expires and expires < now:
                logger.debug("TopicCache: expired topic {}", t.get("topic_id"))
                continue
            valid.append(t)
        if len(valid) != len(topics):
            self._write(valid)

    def adjust_confidence(self, topic_id: str, feedback: str) -> float:
        """Adjust confidence based on user feedback. Returns new confidence."""
        delta = _ADJUSTMENTS.get(feedback, 0.0)
        topics = self.get_topics()
        new_confidence = 0.5
        for t in topics:
            if t["topic_id"] == topic_id:
                old = t.get("confidence", 0.5)
                new_confidence = max(0.1, min(1.0, old + delta))
                t["confidence"] = new_confidence
                break
        self._write(topics)
        return new_confidence

    def on_user_feedback(self, topic_id: str, feedback: str) -> None:
        """Handle user feedback: adjust confidence + remove if rejected."""
        new_conf = self.adjust_confidence(topic_id, feedback)
        if feedback == "reject":
            self.remove_topic(topic_id)
        elif new_conf < _MIN_CONFIDENCE:
            self.remove_topic(topic_id)
            logger.info("TopicCache: removed low-confidence topic {}", topic_id)

    async def push_to_frontend(self, ws_channel) -> None:
        """Push current topics via WebSocket."""
        topics = self.get_topics()
        if not topics:
            return
        if hasattr(ws_channel, "push_topics"):
            await ws_channel.push_topics(topics)

    def _write(self, topics: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(t, ensure_ascii=False) for t in topics]
        self._path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
