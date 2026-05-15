"""Tests for the Policy Engine."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from fincat.agent.policy import (
    HITLEventBus,
    PolicyEngine,
    PolicyResult,
    PolicyViolation,
    SuspendedOperation,
    TradeIntent,
    ActionIntent,
    get_hitl_bus,
)


@pytest.fixture
def policy_config():
    """Create a default policy config for testing."""
    from fincat.config.schema import PolicyConfig, PolicyRiskRules, PolicyPermissionRules, PolicyHITLConfig

    return PolicyConfig(
        enabled=True,
        risk=PolicyRiskRules(
            max_single_trade_value=10000.0,
            max_daily_trade_count=5,
            max_position_concentration=0.3,
            enable_hard_stop_loss=True,
            stop_loss_percentage=0.05,
            enable_daily_loss_limit=True,
            daily_loss_limit_percentage=0.1,
        ),
        permissions=PolicyPermissionRules(
            allow_filesystem_write=True,
            allow_env_modification=False,
            allowed_write_paths=["/workspace/safe"],
        ),
        hitl=PolicyHITLConfig(
            enabled=True,
            suspend_tools=["execute_trade", "transfer_funds"],
            suspend_timeout_seconds=300,
        ),
    )


@pytest.fixture
def disabled_policy_config():
    """Create a disabled policy config."""
    from fincat.config.schema import PolicyConfig

    config = PolicyConfig(enabled=False)
    return config


class TestPolicyEngineTradeChecks:
    """Tests for trade risk rule checks."""

    @pytest.mark.asyncio
    async def test_allow_small_trade(self, policy_config):
        """Small trade under limit should be allowed."""
        engine = PolicyEngine(config=policy_config)
        intent = TradeIntent(
            symbol="AAPL",
            action="BUY",
            quantity=10,
            price=150.0,
            estimated_value=1500.0,
        )
        result, violation = await engine.check_trade(intent)
        assert result == PolicyResult.ALLOW
        assert violation is None

    @pytest.mark.asyncio
    async def test_deny_large_trade(self, policy_config):
        """Trade exceeding single trade limit should be denied."""
        engine = PolicyEngine(config=policy_config)
        intent = TradeIntent(
            symbol="AAPL",
            action="BUY",
            quantity=100,
            price=150.0,
            estimated_value=15000.0,  # Exceeds 10000 limit
        )
        result, violation = await engine.check_trade(intent)
        assert result == PolicyResult.DENY
        assert violation is not None
        assert violation.rule == "single_trade_limit"

    @pytest.mark.asyncio
    async def test_deny_daily_frequency_exceeded(self, policy_config):
        """Trade when daily frequency is exceeded should be denied."""
        from datetime import datetime
        engine = PolicyEngine(config=policy_config)

        # Simulate 5 trades already executed (set both count and reset date to prevent reset)
        engine._daily_trade_count = 5
        engine._daily_reset_date = datetime.utcnow()

        intent = TradeIntent(
            symbol="AAPL",
            action="BUY",
            quantity=10,
            price=150.0,
            estimated_value=1500.0,
        )
        result, violation = await engine.check_trade(intent)
        assert result == PolicyResult.DENY
        assert violation is not None
        assert violation.rule == "daily_frequency"

    @pytest.mark.asyncio
    async def test_disabled_policy_allows_all(self, disabled_policy_config):
        """When policy is disabled, all trades should be allowed."""
        engine = PolicyEngine(config=disabled_policy_config)
        intent = TradeIntent(
            symbol="AAPL",
            action="BUY",
            quantity=100,
            price=1000.0,
            estimated_value=100000.0,
        )
        result, violation = await engine.check_trade(intent)
        assert result == PolicyResult.ALLOW
        assert violation is None

    def test_should_suspend_trade(self, policy_config):
        """Tools in HITL suspend list should require suspension."""
        engine = PolicyEngine(config=policy_config)
        assert engine.should_suspend_trade("execute_trade") is True
        assert engine.should_suspend_trade("transfer_funds") is True
        assert engine.should_suspend_trade("list_dir") is False

    def test_should_suspend_when_hitl_disabled(self):
        """When HITL is disabled, no tools should require suspension."""
        from fincat.config.schema import PolicyConfig, PolicyHITLConfig

        config = PolicyConfig(
            enabled=True,
            hitl=PolicyHITLConfig(enabled=False, suspend_tools=["execute_trade"]),
        )
        engine = PolicyEngine(config=config)
        assert engine.should_suspend_trade("execute_trade") is False


class TestPolicyEnginePermissionChecks:
    """Tests for permission rule checks."""

    @pytest.mark.asyncio
    async def test_allow_filesystem_write_in_allowed_path(self, policy_config):
        """Write to allowed path should be permitted."""
        engine = PolicyEngine(config=policy_config)
        intent = ActionIntent(
            action_type="write_file",
            target="/workspace/safe/file.txt",
        )
        result, violation = await engine.check_permissions(intent)
        assert result == PolicyResult.ALLOW
        assert violation is None

    @pytest.mark.asyncio
    async def test_deny_env_modification(self, policy_config):
        """Environment variable modification should be denied."""
        engine = PolicyEngine(config=policy_config)
        intent = ActionIntent(
            action_type="modify_env",
            target="API_KEY",
            arguments={"name": "API_KEY", "value": "secret"},
        )
        result, violation = await engine.check_permissions(intent)
        assert result == PolicyResult.DENY
        assert violation is not None
        assert violation.rule == "env_modification"

    @pytest.mark.asyncio
    async def test_deny_exec_dangerous_command(self, policy_config):
        """Exec with dangerous commands should be denied."""
        engine = PolicyEngine(config=policy_config)
        intent = ActionIntent(
            action_type="exec",
            target="rm -rf /",
        )
        result, violation = await engine.check_permissions(intent)
        assert result == PolicyResult.DENY
        assert violation is not None
        assert violation.rule == "exec_dangerous"


class TestHITLEventBus:
    """Tests for the HITL event bus."""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        """Subscribers should receive suspension events."""
        bus = HITLEventBus()
        q = bus.subscribe()

        op = SuspendedOperation(
            operation_type="tool_call",
            tool_name="execute_trade",
            arguments={"symbol": "AAPL", "quantity": 10},
        )

        # Publish confirmation in a separate task
        async def confirm():
            await asyncio.sleep(0.1)
            await bus.publish_confirmation(op.id, True)

        # Start confirmation task
        confirm_task = asyncio.create_task(confirm())

        # Wait for result
        result = await bus.publish_suspension(op)
        await confirm_task

        assert result is True

    @pytest.mark.asyncio
    async def test_publish_rejection(self):
        """User rejection should return False."""
        bus = HITLEventBus()
        q = bus.subscribe()

        op = SuspendedOperation(
            operation_type="tool_call",
            tool_name="execute_trade",
            arguments={},
        )

        # Publish rejection in a separate task
        async def reject():
            await asyncio.sleep(0.1)
            await bus.publish_confirmation(op.id, False)

        reject_task = asyncio.create_task(reject())

        result = await bus.publish_suspension(op)
        await reject_task

        assert result is False


class TestSuspendedOperation:
    """Tests for SuspendedOperation dataclass."""

    def test_default_values(self):
        """SuspendedOperation should have sensible defaults."""
        op = SuspendedOperation(
            operation_type="trade",
            tool_name="execute_trade",
            arguments={"symbol": "AAPL"},
        )
        assert op.id is not None
        assert op.status == "pending"
        assert op.confirmed_at is None
        assert op.result is None
        assert isinstance(op.created_at, datetime)

    def test_unique_ids(self):
        """Each SuspendedOperation should have a unique ID."""
        op1 = SuspendedOperation(operation_type="trade", tool_name="t1", arguments={})
        op2 = SuspendedOperation(operation_type="trade", tool_name="t2", arguments={})
        assert op1.id != op2.id
