"""Tests for financial context in ContextBuilder."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from fincat.agent.context import ContextBuilder


class TestBuildFinancialContext:
    """Tests for build_financial_context method."""

    def test_empty_when_no_store(self, tmp_path):
        """Should return empty string when no SQLite store provided."""
        builder = ContextBuilder(workspace=tmp_path)
        result = builder.build_financial_context()
        assert result == ""

    def test_with_financial_profile_only(self, tmp_path):
        """Should include financial profile from SQLite user_profile table."""
        user_md = tmp_path / "USER.md"
        user_md.write_text("# User Profile\n\nName: John\n")

        class MockMemoryStore:
            def get_all_profile_keys(self, category=None):
                if category == "financial":
                    return {"risk_tolerance": "medium", "trading_style": "swing"}
                return {}

        mock_store = MockMemoryStore()
        builder = ContextBuilder(workspace=tmp_path, memory_store=mock_store)
        result = builder.build_financial_context()

        assert "## User Financial Profile" in result
        assert "risk_tolerance" in result
        assert "medium" in result

    def test_with_mock_store_full(self, tmp_path):
        """Should include positions, transactions, and tasks from mock store."""
        user_md = tmp_path / "USER.md"
        user_md.write_text("# User Profile\n\nName: John\n")

        @dataclass
        class MockAsset:
            symbol: str
            quantity: float
            avg_price: float = 150.0

        @dataclass
        class MockTransaction:
            timestamp: str
            action: str
            quantity: float
            symbol: str
            price: float

        @dataclass
        class MockTask:
            task_id: str
            task_type: str
            status: str
            progress: float

        class MockMemoryStore:
            def get_all_profile_keys(self, category=None):
                if category == "financial":
                    return {"risk_tolerance": "high"}
                return {}

            def get_all_assets(self):
                return [MockAsset(symbol="AAPL", quantity=100, avg_price=150.0)]

            def get_transactions(self, days=None):
                return [MockTransaction(timestamp="2024-01-15T10:00:00", action="BUY", quantity=10, symbol="AAPL", price=150.0)]

            def get_active_tasks(self):
                return [MockTask(task_id="task1", task_type="backtest", status="running", progress=0.3)]

        mock_store = MockMemoryStore()
        builder = ContextBuilder(workspace=tmp_path)
        result = builder.build_financial_context(memory_store=mock_store)

        assert "## User Financial Profile" in result
        assert "## Current Positions" in result
        assert "**AAPL**" in result
        assert "## Recent Trades" in result
        assert "## Active Tasks" in result

    def test_with_symbol_filter(self, tmp_path):
        """Should filter by symbols when provided."""
        user_md = tmp_path / "USER.md"
        user_md.write_text("# User Profile\n\nName: John\n")

        @dataclass
        class MockAsset:
            symbol: str
            quantity: float
            avg_price: float = 150.0

        class MockMemoryStore:
            def get_all_profile_keys(self, category=None):
                return {}

            def get_all_assets(self):
                return [
                    MockAsset(symbol="AAPL", quantity=100),
                    MockAsset(symbol="GOOGL", quantity=50),
                ]

        mock_store = MockMemoryStore()
        builder = ContextBuilder(workspace=tmp_path)
        result = builder.build_financial_context(memory_store=mock_store, symbols=["AAPL"])

        assert "**AAPL**" in result
        assert "**GOOGL**" not in result

    def test_symbol_filter_no_match(self, tmp_path):
        """Should return empty sections when no symbols match."""
        user_md = tmp_path / "USER.md"
        user_md.write_text("# User Profile\n\nName: John\n")

        @dataclass
        class MockAsset:
            symbol: str
            quantity: float
            avg_price: float = 150.0

        class MockMemoryStore:
            def get_all_profile_keys(self, category=None):
                return {}

            def get_all_assets(self):
                return [MockAsset(symbol="AAPL", quantity=100)]

        mock_store = MockMemoryStore()
        builder = ContextBuilder(workspace=tmp_path)
        result = builder.build_financial_context(memory_store=mock_store, symbols=["GOOGL"])

        # Assets section should not appear when filtered results are empty
        assert "## Current Positions" not in result


class TestFormatting:
    """Tests for formatting methods."""

    def test_format_assets(self, tmp_path):
        """Should format assets correctly."""
        builder = ContextBuilder(workspace=tmp_path)

        @dataclass
        class MockAsset:
            symbol: str
            quantity: float
            avg_price: float

        assets = [
            MockAsset(symbol="AAPL", quantity=100, avg_price=150.0),
            MockAsset(symbol="GOOGL", quantity=50, avg_price=140.0),
        ]

        result = builder._format_assets(assets)
        assert "## Current Positions" in result
        assert "**AAPL**" in result
        assert "100 shares @ $150.00" in result
        assert "$15000.00" in result  # 100 * 150

    def test_format_transactions(self, tmp_path):
        """Should format transactions correctly."""
        builder = ContextBuilder(workspace=tmp_path)

        @dataclass
        class MockTransaction:
            timestamp: str
            action: str
            quantity: float
            symbol: str
            price: float

        txs = [
            MockTransaction(timestamp="2024-01-15T10:00:00", action="BUY", quantity=10, symbol="AAPL", price=150.0),
        ]

        result = builder._format_transactions(txs)
        assert "## Recent Trades" in result
        assert "2024-01-15 BUY 10 AAPL @ $150.00" in result

    def test_format_tasks(self, tmp_path):
        """Should format tasks correctly."""
        builder = ContextBuilder(workspace=tmp_path)

        @dataclass
        class MockTask:
            task_id: str
            task_type: str
            status: str
            progress: float

        tasks = [
            MockTask(task_id="backtest_001", task_type="backtest", status="running", progress=0.5),
        ]

        result = builder._format_tasks(tasks)
        assert "## Active Tasks" in result
        assert "**backtest_001**" in result
        assert "50%" in result
