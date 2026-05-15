"""Tests for TopicStore and UnifiedTopic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fincat.agent.topic_store import (
    UnifiedTopic,
    TopicStore,
    compute_expires_at,
)


@pytest.fixture
def topic_store(tmp_path: Path) -> TopicStore:
    return TopicStore(tmp_path / "topics.jsonl")


def _make_topic(
    topic_id: str = "t1",
    source: str = "proactive",
    source_name: str = "deadline",
    category: str = "alert",
    title: str = "Test topic",
    content: str = "Test content",
    priority: int = 1,
    **kwargs,
) -> UnifiedTopic:
    return UnifiedTopic(
        topic_id=topic_id,
        source=source,
        source_name=source_name,
        category=category,
        title=title,
        content=content,
        priority=priority,
        **kwargs,
    )


class TestUnifiedTopic:
    def test_to_dict_roundtrip(self):
        now = datetime.now(timezone.utc)
        topic = _make_topic(created_at=now, expires_at=now + timedelta(hours=24))
        d = topic.to_dict()
        restored = UnifiedTopic.from_dict(d)
        assert restored.topic_id == topic.topic_id
        assert restored.title == topic.title
        assert restored.priority == topic.priority

    def test_is_expired(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        topic = _make_topic(expires_at=past)
        assert topic.is_expired()

    def test_not_expired(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        topic = _make_topic(expires_at=future)
        assert not topic.is_expired()

    def test_is_delivered_to(self):
        topic = _make_topic()
        assert not topic.is_delivered_to("cli")
        topic.delivered["cli"] = datetime.now(timezone.utc)
        assert topic.is_delivered_to("cli")


class TestTopicStore:
    def test_add_and_get_pending(self, topic_store: TopicStore):
        topic = _make_topic()
        assert topic_store.add(topic) is True
        pending = topic_store.get_pending("cli")
        assert len(pending) == 1
        assert pending[0].topic_id == "t1"

    def test_dedup(self, topic_store: TopicStore):
        t1 = _make_topic(topic_id="t1", source_name="deadline", title="华为机试今日到期")
        t2 = _make_topic(topic_id="t2", source_name="deadline", title="华为机试今日到期")
        assert topic_store.add(t1) is True
        assert topic_store.add(t2) is False  # dedup

    def test_different_source_name_not_dedup(self, topic_store: TopicStore):
        t1 = _make_topic(topic_id="t1", source_name="deadline", title="Test")
        t2 = _make_topic(topic_id="t2", source_name="high_frequency", title="Test")
        assert topic_store.add(t1) is True
        assert topic_store.add(t2) is True

    def test_mark_delivered(self, topic_store: TopicStore):
        topic = _make_topic()
        topic_store.add(topic)
        topic_store.mark_delivered("t1", "cli")
        pending = topic_store.get_pending("cli")
        assert len(pending) == 0

    def test_cleanup_expired(self, topic_store: TopicStore):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        topic = _make_topic(expires_at=past)
        topic_store.add(topic)
        count = topic_store.cleanup()
        assert count == 1
        assert len(topic_store) == 0

    def test_priority_sorting(self, topic_store: TopicStore):
        t1 = _make_topic(topic_id="t1", priority=1, title="Low")
        t2 = _make_topic(topic_id="t2", priority=3, title="High")
        topic_store.add(t1)
        topic_store.add(t2)
        pending = topic_store.get_pending("cli")
        assert pending[0].priority == 3
        assert pending[1].priority == 1

    def test_persistence(self, tmp_path: Path):
        path = tmp_path / "topics.jsonl"
        store1 = TopicStore(path)
        store1.add(_make_topic(topic_id="persist1"))
        # Reload from disk
        store2 = TopicStore(path)
        assert len(store2) == 1
        assert store2.get_pending("cli")[0].topic_id == "persist1"


class TestComputeExpiresAt:
    def test_alert_expiry(self):
        expires = compute_expires_at("alert")
        assert expires is not None
        assert expires > datetime.now(timezone.utc)

    def test_news_expiry(self):
        expires = compute_expires_at("news")
        assert expires is not None

    def test_reminder_with_deadline(self):
        expires = compute_expires_at("reminder", {"deadline": "2026-05-10T00:00:00"})
        assert expires is not None
        assert expires.day == 10

    def test_insight_expiry(self):
        expires = compute_expires_at("insight")
        assert expires is not None
