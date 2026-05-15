"""Integration tests for the full financial memory flow."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from fincat.agent.events import EventBus, TradeExecutedEvent
from fincat.agent.memory_sqlite import SQLiteMemoryStore
from fincat.agent.reflection import ReflectionGenerator
from fincat.agent.tools.trading import ExecuteTradeTool, MockBrokerAdapter


@pytest.fixture
def sqlite_store(tmp_path):
    """SQLiteMemoryStore with a temp database."""
    db_path = tmp_path / "test.db"
    return SQLiteMemoryStore(db_path)


@pytest.fixture
def event_bus():
    """Fresh EventBus instance."""
    return EventBus()


class TestEventBus:
    """Tests for EventBus and TradeExecutedEvent."""

    def test_publish_and_receive(self):
        """Should deliver events to subscribed handlers."""
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(TradeExecutedEvent, handler)

        async def test():
            await bus.publish(TradeExecutedEvent(
                symbol="AAPL", action="BUY", quantity=100,
                price=150.0, order_id="123", timestamp=datetime.now()
            ))

        asyncio.run(test())

        assert len(received) == 1
        assert received[0].symbol == "AAPL"
        assert received[0].action == "BUY"

    def test_multiple_handlers(self):
        """Should notify all subscribed handlers."""
        bus = EventBus()
        received1, received2 = [], []

        async def handler1(event):
            received1.append(event)

        async def handler2(event):
            received2.append(event)

        bus.subscribe(TradeExecutedEvent, handler1)
        bus.subscribe(TradeExecutedEvent, handler2)

        async def test():
            await bus.publish(TradeExecutedEvent(
                symbol="TSLA", action="SELL", quantity=50,
                price=200.0, order_id="456", timestamp=datetime.now()
            ))

        asyncio.run(test())

        assert len(received1) == 1
        assert len(received2) == 1


class TestExecuteTradeToolMemory:
    """Tests for ExecuteTradeTool writing to memory and publishing events."""

    @pytest.mark.asyncio
    async def test_successful_trade_writes_transaction(self, sqlite_store, event_bus):
        """Should write to transactions table on successful trade."""
        received = []

        async def on_trade(event):
            received.append(event)

        event_bus.subscribe(TradeExecutedEvent, on_trade)

        tool = ExecuteTradeTool(
            broker=MockBrokerAdapter(),
            memory_store=sqlite_store,
            event_bus=event_bus,
        )

        result = await tool.execute(
            symbol="AAPL", action="buy", quantity=100,
            order_type="market", limit_price=150.0
        )

        assert "Order submitted successfully" in result

        txs = sqlite_store.get_transactions(symbol="AAPL")
        assert len(txs) == 1
        assert txs[0].action == "BUY"
        assert txs[0].quantity == 100

        assert len(received) == 1
        assert received[0].symbol == "AAPL"
        assert received[0].action == "BUY"

    @pytest.mark.asyncio
    async def test_trade_without_memory_store(self):
        """Should not crash when memory_store is None."""
        bus = EventBus()
        tool = ExecuteTradeTool(
            broker=MockBrokerAdapter(),
            memory_store=None,
            event_bus=bus,
        )

        result = await tool.execute(
            symbol="GOOGL", action="buy", quantity=50,
            order_type="market", limit_price=140.0
        )

        assert "Order submitted successfully" in result


class TestReflectionGenerator:
    """Tests for ReflectionGenerator."""

    @pytest.mark.asyncio
    async def test_generates_reflection_on_trade(self, sqlite_store, event_bus):
        """Should generate and store reflection when trade is executed."""
        called = []

        class MockProvider:
            async def chat_with_retry(self, model, messages, tools, tool_choice):
                called.append(messages)
                @dataclass
                class Resp:
                    content: str
                return Resp(content="追高容易被套，注意止损纪律")

        gen = ReflectionGenerator(
            event_bus=event_bus,
            memory_store=sqlite_store,
            provider=MockProvider(),
            model="test",
        )
        await gen.start()

        await event_bus.publish(TradeExecutedEvent(
            symbol="NVDA", action="BUY", quantity=100,
            price=500.0, order_id="NVDA001", timestamp=datetime.now()
        ))

        # Allow async processing
        await asyncio.sleep(0.3)

        reflections = sqlite_store.search_reflections(query_text="止损", top_k=5)
        assert len(reflections) >= 1
        assert reflections[0].tags is not None
        assert "trade" in reflections[0].tags
        assert reflections[0].reflection_type == "trade_review"

        await gen.stop()


class TestFullMemoryFlow:
    """End-to-end tests for the complete memory flow."""

    @pytest.mark.asyncio
    async def test_trade_to_reflection_full_flow(self, sqlite_store, event_bus):
        """Complete flow: trade → transaction → event → reflection."""
        reflection_generated = []

        class MockProvider:
            async def chat_with_retry(self, model, messages, tools, tool_choice):
                @dataclass
                class Resp:
                    content: str
                reflection_generated.append(True)
                return Resp(content="突破买入注意回调风险")

        gen = ReflectionGenerator(
            event_bus=event_bus,
            memory_store=sqlite_store,
            provider=MockProvider(),
            model="test",
        )
        await gen.start()

        tool = ExecuteTradeTool(
            broker=MockBrokerAdapter(),
            memory_store=sqlite_store,
            event_bus=event_bus,
        )

        # Execute a trade
        await tool.execute(
            symbol="AMD", action="buy", quantity=200,
            order_type="limit", limit_price=120.0
        )

        # Wait for async reflection generation
        await asyncio.sleep(0.3)

        # Verify transaction was written
        txs = sqlite_store.get_transactions(symbol="AMD")
        assert len(txs) == 1

        # Verify reflection was generated
        assert len(reflection_generated) == 1

        reflections = sqlite_store.search_reflections(query_text="回调", top_k=5)
        assert len(reflections) >= 1

        await gen.stop()
