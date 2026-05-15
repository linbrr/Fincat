"""Tests for ProactiveSkill analysis engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fincat.agent.memory_item import MemoryItem, MemoryItemStore
from fincat.agent.signal_store import SignalStore
from fincat.agent.topic_store import TopicStore
from fincat.agent.proactive_skill import ProactiveSkill


@pytest.fixture
def item_store(tmp_path: Path) -> MemoryItemStore:
    return MemoryItemStore(tmp_path / "items.jsonl")


@pytest.fixture
def topic_store(tmp_path: Path) -> TopicStore:
    return TopicStore(tmp_path / "topics.jsonl")


@pytest.fixture
def mock_memory() -> MagicMock:
    m = MagicMock()
    m.read_category.return_value = ""
    return m


@pytest.fixture
def mock_snapshots() -> MagicMock:
    s = MagicMock()
    s.load_latest.return_value = None
    return s


@pytest.fixture
def proactive_skill(item_store, topic_store, mock_memory, mock_snapshots) -> ProactiveSkill:
    signals = SignalStore(
        item_store=item_store,
        memory=mock_memory,
        snapshot_store=mock_snapshots,
    )
    return ProactiveSkill(signals=signals, topic_store=topic_store)


class TestProactiveSkill:
    def test_analyze_realtime_no_topics(self, proactive_skill, topic_store):
        topics = proactive_skill.analyze("realtime")
        assert len(topics) == 0
        assert len(topic_store) == 0

    def test_high_frequency_strategy(self, proactive_skill, item_store, topic_store):
        for i in range(5):
            item_store.add("频繁关注黄金", "preference", source_session=f"s{i}")
        topics = proactive_skill.analyze("realtime")
        hf_topics = [t for t in topics if t.source_name == "high_frequency"]
        assert len(hf_topics) >= 1

    def test_interest_decay_strategy(self, proactive_skill, item_store, topic_store):
        item = item_store.add("旧关注话题", "knowledge", source_session="s1")
        item.frequency = 3
        item.decay_score = 0.1
        item.last_accessed = datetime.now(timezone.utc) - timedelta(days=60)
        item_store._rewrite()
        topics = proactive_skill.analyze("realtime")
        decay_topics = [t for t in topics if t.source_name == "interest_decay"]
        assert len(decay_topics) >= 1

    def test_pattern_to_cron_weekly(self):
        pattern = {"type": "weekly", "time": "Friday 15:00"}
        cron = ProactiveSkill._pattern_to_cron(pattern)
        assert cron is not None
        assert "15" in cron
        assert "5" in cron

    def test_pattern_to_cron_daily(self):
        pattern = {"type": "daily", "time": "09:00"}
        cron = ProactiveSkill._pattern_to_cron(pattern)
        assert cron is not None
        assert "9" in cron

    def test_extract_pattern(self):
        content = "周期: weekly | 时间: 周五 15:00"
        pattern = ProactiveSkill._extract_pattern(content)
        assert pattern is not None
        assert pattern["type"] == "weekly"

    def test_extract_pattern_none(self):
        content = "no pattern here"
        pattern = ProactiveSkill._extract_pattern(content)
        assert pattern is None

    def test_auto_cron_candidates(self, proactive_skill, item_store):
        for i in range(5):
            item_store.add("周期: weekly | 时间: 周五 15:00", "behavior", source_session=f"s{i}")
        for item in item_store.query(category="behavior"):
            item.confidence = 0.9
        item_store._rewrite()
        candidates = proactive_skill.check_auto_cron()
        assert len(candidates) >= 1
        assert "schedule" in candidates[0]
