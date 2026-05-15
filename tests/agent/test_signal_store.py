"""Tests for SignalStore."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fincat.agent.memory_item import MemoryItem, MemoryItemStore
from fincat.agent.event_sources import ExternalEvent
from fincat.agent.signal_store import SignalStore


@pytest.fixture
def item_store(tmp_path: Path) -> MemoryItemStore:
    return MemoryItemStore(tmp_path / "items.jsonl")


@pytest.fixture
def mock_memory() -> MagicMock:
    m = MagicMock()
    m.read_category.return_value = "test content"
    return m


@pytest.fixture
def mock_snapshots() -> MagicMock:
    s = MagicMock()
    s.load_latest.return_value = None
    return s


@pytest.fixture
def signal_store(item_store, mock_memory, mock_snapshots) -> SignalStore:
    return SignalStore(
        item_store=item_store,
        memory=mock_memory,
        snapshot_store=mock_snapshots,
    )


class TestSignalStore:
    def test_get_active_interests(self, signal_store, item_store):
        item_store.add("关注黄金", "preference", source_session="s1")
        interests = signal_store.get_active_interests(min_decay=0.1)
        assert len(interests) >= 1

    def test_get_high_frequency_items(self, signal_store, item_store):
        for i in range(5):
            item_store.add("频繁话题", "knowledge", source_session=f"s{i}")
        items = signal_store.get_high_frequency_items(threshold=3)
        assert len(items) >= 1
        assert items[0].frequency >= 3

    def test_get_behavior_patterns(self, signal_store, item_store):
        item_store.add("周五活跃", "behavior", source_session="s1")
        patterns = signal_store.get_behavior_patterns()
        assert len(patterns) == 1

    def test_get_category_text(self, signal_store, mock_memory):
        text = signal_store.get_category_text("preference")
        assert text == "test content"
        mock_memory.read_category.assert_called_with("preference")

    def test_event_cache_add_and_get(self, signal_store):
        event = ExternalEvent(
            event_id="e1",
            event_type="market_move",
            title="贵州茅台涨5%",
            summary="茅台今日涨幅超5%",
            entities=["stock_600519"],
            severity="warning",
            source="akshare",
            timestamp=datetime.now(timezone.utc),
        )
        signal_store.add_event(event)
        events = signal_store.get_recent_events(hours=1)
        assert len(events) == 1
        assert events[0].title == "贵州茅台涨5%"

    def test_event_cache_expiry(self, signal_store):
        old_event = ExternalEvent(
            event_id="e_old",
            event_type="market_move",
            title="Old event",
            summary="Old",
            entities=[],
            severity="info",
            source="akshare",
            timestamp=datetime.now(timezone.utc) - timedelta(hours=5),
        )
        signal_store._event_cache.append(old_event)
        signal_store._evict_events()
        events = signal_store.get_recent_events(hours=4)
        assert len(events) == 0

    def test_get_entity_trends_empty(self, signal_store):
        trends = signal_store.get_entity_trends()
        assert trends == {}
