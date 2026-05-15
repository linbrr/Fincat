"""EventTopicBridge — converts ExternalEvents to UnifiedTopics for frontend dispatch."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from loguru import logger

from fincat.agent.event_sources import EventQueue, ExternalEvent
from fincat.agent.topic.topic_store import TopicStore, UnifiedTopic, compute_expires_at


# Severity → priority mapping
_SEVERITY_PRIORITY = {
    "critical": 3,
    "warning": 2,
    "info": 1,
}

# Event type → topic category mapping
_EVENT_CATEGORY = {
    "market_move": "alert",
    "new_document": "news",
    "regulation_change": "alert",
    "custom": "insight",
}


class EventTopicBridge:
    """Bridges ExternalEvents from EventQueue into TopicStore as UnifiedTopics."""

    def __init__(self, event_queue: EventQueue, topic_store: TopicStore):
        self._queue = event_queue
        self._store = topic_store

    async def drain(self) -> int:
        """Drain all pending events from the queue and convert to topics.

        Returns the number of topics created.
        """
        count = 0
        while True:
            event = self._queue.pop_sync()
            if event is None:
                break
            topic = self._convert(event)
            if topic and self._store.add(topic):
                count += 1
        if count:
            logger.info("EventTopicBridge: {} event(s) → topic(s)", count)
        return count

    def _convert(self, event: ExternalEvent) -> UnifiedTopic | None:
        """Convert an ExternalEvent to a UnifiedTopic."""
        category = _EVENT_CATEGORY.get(event.event_type, "insight")
        priority = _SEVERITY_PRIORITY.get(event.severity, 1)

        return UnifiedTopic(
            topic_id=f"evt_{uuid.uuid4().hex[:8]}",
            source="event",
            source_name=event.source,
            category=category,
            title=event.title,
            content=event.summary,
            priority=priority,
            entities=event.entities,
            created_at=event.timestamp or datetime.now(timezone.utc),
            expires_at=compute_expires_at(category),
            metadata={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "severity": event.severity,
                **event.metadata,
            },
        )
