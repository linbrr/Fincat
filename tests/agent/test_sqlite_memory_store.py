"""Tests for SQLiteMemoryStore."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta

import pytest

from fincat.agent.memory_sqlite import (
    SQLiteMemoryStore,
    Asset,
    Transaction,
    ActiveTask,
    Reflection,
    KnowledgeEntry,
)


@pytest.fixture
def store(tmp_path):
    """Create a temporary SQLite memory store for testing."""
    db_path = tmp_path / "test_memory.db"
    return SQLiteMemoryStore(db_path)


class TestAssets:
    """Tests for assets operations."""

    def test_upsert_asset(self, store):
        """Should insert and update assets."""
        # Insert
        asset = store.upsert_asset("AAPL", quantity=100, cost_basis=15000)
        assert asset.symbol == "AAPL"
        assert asset.quantity == 100
        assert asset.cost_basis == 15000
        assert asset.avg_price == 150.0

        # Update
        updated = store.upsert_asset("AAPL", quantity=150, cost_basis=22500)
        assert updated.quantity == 150
        assert updated.avg_price == 150.0

    def test_get_asset(self, store):
        """Should retrieve asset by symbol."""
        store.upsert_asset("TSLA", quantity=50, cost_basis=10000)
        asset = store.get_asset("TSLA")
        assert asset is not None
        assert asset.symbol == "TSLA"
        assert asset.quantity == 50

    def test_get_asset_not_found(self, store):
        """Should return None for non-existent asset."""
        asset = store.get_asset("NONEXISTENT")
        assert asset is None

    def test_get_all_assets(self, store):
        """Should return all assets."""
        store.upsert_asset("AAPL", quantity=100, cost_basis=15000)
        store.upsert_asset("GOOGL", quantity=20, cost_basis=3000)
        assets = store.get_all_assets()
        assert len(assets) == 2
        symbols = {a.symbol for a in assets}
        assert symbols == {"AAPL", "GOOGL"}

    def test_delete_asset(self, store):
        """Should delete an asset."""
        store.upsert_asset("AAPL", quantity=100, cost_basis=15000)
        result = store.delete_asset("AAPL")
        assert result is True
        assert store.get_asset("AAPL") is None

        # Non-existent
        result = store.delete_asset("NONEXISTENT")
        assert result is False


class TestTransactions:
    """Tests for transactions operations."""

    def test_add_transaction(self, store):
        """Should add a transaction."""
        tx = store.add_transaction(
            symbol="AAPL",
            action="BUY",
            quantity=10,
            price=150.0,
            decision_reason="低估",
        )
        assert tx.symbol == "AAPL"
        assert tx.action == "BUY"
        assert tx.quantity == 10
        assert tx.price == 150.0
        assert tx.total_amount == 1500.0
        assert tx.decision_reason == "低估"

    def test_get_transactions_by_symbol(self, store):
        """Should filter transactions by symbol."""
        store.add_transaction("AAPL", "BUY", 10, 150)
        store.add_transaction("GOOGL", "BUY", 5, 140)
        store.add_transaction("AAPL", "SELL", 5, 160)

        txs = store.get_transactions(symbol="AAPL")
        assert len(txs) == 2
        assert all(tx.symbol == "AAPL" for tx in txs)

    def test_get_transactions_by_days(self, store):
        """Should filter transactions by days."""
        old_date = (datetime.now() - timedelta(days=60)).isoformat()
        store.add_transaction("AAPL", "BUY", 10, 150)
        store.add_transaction("AAPL", "BUY", 10, 150, timestamp=old_date)

        txs_30 = store.get_transactions(days=30)
        txs_90 = store.get_transactions(days=90)
        assert len(txs_30) == 1
        assert len(txs_90) == 2

    def test_search_transactions(self, store):
        """Should full-text search transactions."""
        store.add_transaction("AAPL", "BUY", 10, 150, decision_reason="技术分析突破")
        store.add_transaction("GOOGL", "BUY", 5, 140, decision_reason="价值投资")

        results = store.search_transactions("技术分析")
        assert len(results) == 1
        assert results[0].symbol == "AAPL"


class TestActiveTasks:
    """Tests for active tasks operations."""

    def test_create_task(self, store):
        """Should create a task."""
        task = store.create_task("backtest_001", "backtest", {"symbol": "AAPL"})
        assert task.task_id == "backtest_001"
        assert task.task_type == "backtest"
        assert task.status == "pending"

    def test_update_task(self, store):
        """Should update task status and progress."""
        store.create_task("backtest_001", "backtest")
        task = store.update_task("backtest_001", status="running", progress=0.5)

        assert task is not None
        assert task.status == "running"
        assert task.progress == 0.5

    def test_get_active_tasks(self, store):
        """Should get only active (non-completed) tasks."""
        store.create_task("task_1", "backtest")
        store.create_task("task_2", "backtest")
        store.update_task("task_1", status="completed")

        active = store.get_active_tasks()
        assert len(active) == 1
        assert active[0].task_id == "task_2"


class TestUserProfile:
    """Tests for user profile operations."""

    def test_set_and_get_profile(self, store):
        """Should set and get profile values."""
        store.set_profile("risk_tolerance", "high", category="trading")
        store.set_profile("name", "John", category="user")

        assert store.get_profile("risk_tolerance") == "high"
        assert store.get_profile("name") == "John"
        assert store.get_profile("nonexistent") is None

    def test_get_all_profile_keys(self, store):
        """Should get all profile keys."""
        store.set_profile("risk_tolerance", "high", category="trading")
        store.set_profile("name", "John", category="user")

        all_keys = store.get_all_profile_keys()
        assert len(all_keys) == 2

        trading_keys = store.get_all_profile_keys(category="trading")
        assert len(trading_keys) == 1
        assert "risk_tolerance" in trading_keys


class TestKnowledgeBase:
    """Tests for knowledge base operations."""

    def test_add_knowledge(self, store):
        """Should add a knowledge entry."""
        entry = store.add_knowledge(
            title="AAPL 分析",
            content="苹果是一家优秀的科技公司",
            tags=["股票", "科技"],
        )
        assert entry.title == "AAPL 分析"
        assert entry.tags == ["股票", "科技"]

    def test_search_knowledge(self, store):
        """Should full-text search knowledge base."""
        store.add_knowledge("AAPL 分析", "苹果公司财务分析", tags=["股票"])
        store.add_knowledge("GOOGL 分析", "谷歌搜索引擎优势", tags=["股票"])

        results = store.search_knowledge("苹果")
        assert len(results) == 1
        assert results[0].title == "AAPL 分析"

    def test_search_knowledge_by_tag(self, store):
        """Should filter knowledge by tags."""
        store.add_knowledge("AAPL", "苹果公司分析报告", tags=["科技", "分析"])
        store.add_knowledge("TSLA", "特斯拉公司分析报告", tags=["汽车", "分析"])

        # Search with both query and tag filter
        results = store.search_knowledge("苹果", tags=["科技"])
        assert len(results) == 1
        assert results[0].title == "AAPL"

        # Search with only tag filter (query empty string)
        results2 = store.search_knowledge("", tags=["汽车"])
        assert len(results2) == 1
        assert results2[0].title == "TSLA"


class TestReflectionVault:
    """Tests for reflection vault operations."""

    def test_add_reflection(self, store):
        """Should add a reflection."""
        content = "某次追高失败，教训是需要结合波动率分析"
        refl = store.add_reflection(
            content=content,
            tags=["止损", "教训"],
            reflection_type="trade_loss",
        )
        assert refl.content == content
        assert refl.reflection_type == "trade_loss"

    def test_search_reflections_text(self, store):
        """Should search reflections by text."""
        store.add_reflection("某次追高失败", reflection_type="trade_loss")
        store.add_reflection("保持纪律", reflection_type="general")

        results = store.search_reflections(query_text="追高")
        assert len(results) == 1
        assert "追高" in results[0].content

    def test_search_reflections_type(self, store):
        """Should filter reflections by type."""
        store.add_reflection("失败1", reflection_type="trade_loss")
        store.add_reflection("失败2", reflection_type="trade_loss")
        store.add_reflection("成功1", reflection_type="trade_win")

        results = store.search_reflections(reflection_type="trade_loss")
        assert len(results) == 2


class TestVacuum:
    """Tests for utility operations."""

    def test_vacuum(self, store):
        """Should run vacuum without error."""
        store.add_transaction("AAPL", "BUY", 10, 150)
        store.delete_asset("NONEXISTENT")
        store.vacuum()  # Should not raise
