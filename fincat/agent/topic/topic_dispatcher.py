"""TopicDispatcher — polls TopicStore and delivers to all enabled channels.

Zero token cost — pure Python dispatch loop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Protocol

from loguru import logger

from .topic_store import TopicStore, UnifiedTopic


class ChannelSender(Protocol):
    """Protocol for sending topic payloads to a channel."""

    async def send_topics(self, topics: list[UnifiedTopic]) -> None: ...


class TopicDispatcher:
    """Polls TopicStore every N seconds, dispatches pending topics to channels."""

    def __init__(
        self,
        store: TopicStore,
        poll_interval: float = 30.0,
    ):
        self._store = store
        self._interval = poll_interval
        self._channels: dict[str, ChannelSender] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._on_connect_prepare: Any = None

    def register_channel(self, name: str, sender: ChannelSender) -> None:
        """Register a channel for topic dispatch."""
        self._channels[name] = sender
        logger.debug("TopicDispatcher: registered channel '{}'", name)

    def set_connect_prepare(self, fn: Any) -> None:
        """Register a callable to run before pushing topics on new connections."""
        self._on_connect_prepare = fn

    async def on_client_connect(self, chat_id: str) -> None:
        """Run analysis then send pending topics to a newly connected client."""
        logger.info("TopicDispatcher: on_client_connect chat_id={}", chat_id)
        # Run proactive analysis to populate store if empty
        if self._on_connect_prepare:
            try:
                result = self._on_connect_prepare()
                if asyncio.iscoroutine(result):
                    await result
                logger.info("TopicDispatcher: on_connect_prepare done, store has {} topics", len(self._store))
            except Exception:
                logger.exception("TopicDispatcher: on_connect_prepare failed")
        for channel_name, sender in self._channels.items():
            if not hasattr(sender, "send_topics_to_connection"):
                logger.warning("TopicDispatcher: channel '{}' has no send_topics_to_connection", channel_name)
                continue
            # Get ALL non-expired topics (not just undelivered) for initial push
            now = datetime.now(timezone.utc)
            all_topics = [
                t for t in self._store._topics.values()
                if not t.is_expired(now)
            ]
            all_topics.sort(key=lambda t: (-t.priority, t.created_at))
            logger.info("TopicDispatcher: channel '{}' sending {} topics on connect", channel_name, len(all_topics))
            if not all_topics:
                continue
            try:
                await sender.send_topics_to_connection(chat_id, all_topics)
                logger.info(
                    "TopicDispatcher: initial push {} topic(s) to '{}' chat_id={}",
                    len(all_topics), channel_name, chat_id,
                )
            except Exception:
                logger.exception("TopicDispatcher: initial push failed for '{}'", channel_name)

    async def handle_raw_message(self, data: dict[str, Any], chat_id: str) -> bool:
        """Handle raw inbound messages. Returns True if handled."""
        action = data.get("action")

        if action == "topic_feedback":
            topic_id = data.get("topic_id", "")
            feedback = data.get("feedback", "")
            topic = self._store._topics.get(topic_id)
            if topic:
                if feedback in ("dismiss", "reject"):
                    self._store.remove_topic(topic_id)
                elif feedback == "click":
                    topic.metadata["clicked"] = True
                    self._store._rewrite()
                logger.debug("TopicDispatcher: feedback '{}' for topic {}", feedback, topic_id)
            return True

        if action != "request_topics":
            return False
        for channel_name, sender in self._channels.items():
            if not hasattr(sender, "send_topics_to_connection"):
                continue
            now = datetime.now(timezone.utc)
            all_topics = [
                t for t in self._store._topics.values()
                if not t.is_expired(now)
            ]
            all_topics.sort(key=lambda t: (-t.priority, t.created_at))
            if not all_topics:
                continue
            try:
                await sender.send_topics_to_connection(chat_id, all_topics)
            except Exception:
                logger.exception("TopicDispatcher: request_topics failed for '{}'", channel_name)
        return True

    async def start(self) -> None:
        """Start the dispatch loop."""
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("TopicDispatcher started (interval={}s, channels={})", self._interval, list(self._channels.keys()))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("TopicDispatcher tick failed")
            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        """Single dispatch cycle."""
        self._store.cleanup()
        for channel_name, sender in self._channels.items():
            pending = self._store.get_pending(channel_name)
            if not pending:
                continue
            try:
                await sender.send_topics(pending)
                for topic in pending:
                    self._store.mark_delivered(topic.topic_id, channel_name)
                logger.debug("TopicDispatcher: delivered {} topic(s) to '{}'", len(pending), channel_name)
            except Exception:
                logger.exception("TopicDispatcher: failed to deliver to '{}'", channel_name)
