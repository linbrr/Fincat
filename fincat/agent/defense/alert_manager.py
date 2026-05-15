"""Risk Alert Manager — alert management for financial risk events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
from loguru import logger

from fincat.agent.defense.risk_scorer import RiskLevel, RiskSignal


class AlertChannel(Enum):
    """Available alert channels."""
    WECOM = "wecom"           # 企业微信
    DINGTALK = "dingtalk"     # 钉钉
    SMS = "sms"              # 短信
    PHONE = "phone"          # 电话


@dataclass
class AlertPayload:
    """Alert payload for risk events."""
    session_key: str
    risk_level: RiskLevel
    signals: list[RiskSignal]
    context: dict[str, Any]
    timestamp: str


class BaseNotifier:
    """Base class for alert notifiers."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    async def send(self, payload: AlertPayload) -> bool:
        """Send alert notification.

        Args:
            payload: Alert payload

        Returns:
            True if sent successfully, False otherwise
        """
        raise NotImplementedError


class WeComNotifier(BaseNotifier):
    """企业微信 webhook notifier."""

    async def send(self, payload: AlertPayload) -> bool:
        """Send via 企业微信 webhook."""
        webhook_url = self.config.get("webhook_url")
        if not webhook_url:
            logger.warning("WeCom webhook_url not configured")
            return False

        risk_emoji = {
            RiskLevel.P0_CRITICAL: "🔴",
            RiskLevel.P1_HIGH: "🟠",
            RiskLevel.P2_MEDIUM: "🟡",
            RiskLevel.P3_LOW: "🟢",
        }.get(payload.risk_level, "⚪")

        message = self._format_message(payload, risk_emoji)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url,
                    json={"msgtype": "text", "text": {"content": message}},
                )
                if response.status_code == 200:
                    logger.info("WeCom alert sent for session {}", payload.session_key)
                    return True
                else:
                    logger.warning("WeCom alert failed: status={}", response.status_code)
                    return False
        except Exception as e:
            logger.exception("WeCom alert error: {}", e)
            return False

    def _format_message(self, payload: AlertPayload, emoji: str) -> str:
        """Format alert message."""
        signal_list = "\n".join(
            f"  - [{s.category.value}] {s.trigger}"
            for s in payload.signals
        )
        return (
            f"{emoji}【{payload.risk_level.name}风险告警】会话 {payload.session_key}\n"
            f"⏰ 时间: {payload.timestamp}\n"
            f"📋 触发信号:\n{signal_list}\n"
            f"🔔 请立即介入处理"
        )


class DingTalkNotifier(BaseNotifier):
    """钉钉 webhook notifier."""

    async def send(self, payload: AlertPayload) -> bool:
        """Send via 钉钉 webhook."""
        webhook_url = self.config.get("webhook_url")
        if not webhook_url:
            logger.warning("DingTalk webhook_url not configured")
            return False

        risk_emoji = {
            RiskLevel.P0_CRITICAL: "🔴",
            RiskLevel.P1_HIGH: "🟠",
            RiskLevel.P2_MEDIUM: "🟡",
            RiskLevel.P3_LOW: "🟢",
        }.get(payload.risk_level, "⚪")

        message = self._format_message(payload, risk_emoji)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook_url,
                    json={
                        "msgtype": "text",
                        "text": {"content": message},
                    },
                )
                if response.status_code == 200:
                    logger.info("DingTalk alert sent for session {}", payload.session_key)
                    return True
                else:
                    logger.warning("DingTalk alert failed: status={}", response.status_code)
                    return False
        except Exception as e:
            logger.exception("DingTalk alert error: {}", e)
            return False

    def _format_message(self, payload: AlertPayload, emoji: str) -> str:
        """Format alert message."""
        signal_list = "\n".join(
            f"  - {s.trigger}"
            for s in payload.signals[:5]  # Limit to 5 signals
        )
        return (
            f"{emoji} {payload.risk_level.name}风险告警\n"
            f"会话: {payload.session_key}\n"
            f"时间: {payload.timestamp}\n"
            f"信号:\n{signal_list}"
        )


class SMSNotifier(BaseNotifier):
    """SMS notifier (placeholder for actual SMS gateway)."""

    async def send(self, payload: AlertPayload) -> bool:
        """Send SMS alert (Phase 2: integrate with SMS gateway)."""
        # TODO: Integrate with SMS gateway (e.g., Tencent Cloud, Alibaba Cloud)
        phone_numbers = self.config.get("phone_numbers", [])
        if not phone_numbers:
            logger.warning("SMS phone_numbers not configured")
            return False

        message = (
            f"【风险告警】会话 {payload.session_key} 触发 {payload.risk_level.name} 级风险，"
            f"信号: {', '.join(s.trigger for s in payload.signals[:3])}"
        )

        logger.info(
            "SMS alert would be sent to {}: {}",
            len(phone_numbers),
            message,
        )
        # Actual SMS sending would be implemented here
        return True


class RiskAlertManager:
    """Risk alert manager with multi-channel support.

    Manages alert dispatching based on risk levels and channels.

    Usage:
        manager = RiskAlertManager()
        await manager.send_alert(session_key, RiskLevel.P1_HIGH, signals, context)
    """

    def __init__(self):
        self._notifiers: dict[AlertChannel, BaseNotifier] = {}
        self._alert_history: list[AlertPayload] = []
        self._alert_history_limit = 1000

    def register_notifier(self, channel: AlertChannel, notifier: BaseNotifier) -> None:
        """Register a notifier for a channel.

        Args:
            channel: Alert channel
            notifier: Notifier instance
        """
        self._notifiers[channel] = notifier
        logger.info("Registered notifier for channel: {}", channel.value)

    def configure_wecom(self, webhook_url: str) -> None:
        """Configure 企业微信 notifier."""
        self.register_notifier(AlertChannel.WECOM, WeComNotifier({"webhook_url": webhook_url}))

    def configure_dingtalk(self, webhook_url: str) -> None:
        """Configure 钉钉 notifier."""
        self.register_notifier(AlertChannel.DINGTALK, DingTalkNotifier({"webhook_url": webhook_url}))

    def configure_sms(self, phone_numbers: list[str]) -> None:
        """Configure SMS notifier."""
        self.register_notifier(AlertChannel.SMS, SMSNotifier({"phone_numbers": phone_numbers}))

    async def send_alert(
        self,
        session_key: str,
        risk_level: RiskLevel,
        signals: list[RiskSignal],
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Send alert for a risk event.

        Args:
            session_key: Session identifier
            risk_level: Risk level
            signals: Detected risk signals
            context: Additional context

        Returns:
            True if at least one channel succeeded
        """
        if risk_level == RiskLevel.P3_LOW:
            # No alert for low risk
            return False

        payload = AlertPayload(
            session_key=session_key,
            risk_level=risk_level,
            signals=signals,
            context=context or {},
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Determine channels based on risk level
        channels = self._get_channels_for_level(risk_level)

        if not channels:
            logger.debug("No alert channels configured for level {}", risk_level.name)
            return False

        # Send to all configured channels in parallel
        results = await asyncio.gather(
            *[
                self._send_to_channel(channel, payload)
                for channel in channels
                if channel in self._notifiers
            ],
            return_exceptions=True,
        )

        success = any(r is True for r in results)

        # Store in history
        self._add_to_history(payload, success)

        return success

    async def _send_to_channel(
        self,
        channel: AlertChannel,
        payload: AlertPayload,
    ) -> bool:
        """Send alert to a specific channel."""
        notifier = self._notifiers.get(channel)
        if not notifier:
            return False

        try:
            return await notifier.send(payload)
        except Exception as e:
            logger.exception("Alert channel {} failed: {}", channel.value, e)
            return False

    def _get_channels_for_level(self, level: RiskLevel) -> list[AlertChannel]:
        """Get channels to use for a risk level."""
        if level == RiskLevel.P0_CRITICAL:
            return [AlertChannel.WECOM, AlertChannel.DINGTALK, AlertChannel.SMS]
        elif level == RiskLevel.P1_HIGH:
            return [AlertChannel.WECOM]
        elif level == RiskLevel.P2_MEDIUM:
            return []  # No immediate alert, just batch review
        else:
            return []

    def _add_to_history(self, payload: AlertPayload, success: bool) -> None:
        """Add alert to history."""
        self._alert_history.append(payload)
        if len(self._alert_history) > self._alert_history_limit:
            self._alert_history = self._alert_history[-self._alert_history_limit:]

    def get_alert_history(
        self,
        session_key: str | None = None,
        limit: int = 100,
    ) -> list[AlertPayload]:
        """Get alert history.

        Args:
            session_key: Optional filter by session
            limit: Maximum number of results

        Returns:
            List of alert payloads
        """
        history = self._alert_history
        if session_key:
            history = [h for h in history if h.session_key == session_key]
        return history[-limit:]
