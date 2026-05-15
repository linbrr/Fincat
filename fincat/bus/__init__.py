"""Message bus module for decoupled channel-agent communication."""

from fincat.bus.events import InboundMessage, OutboundMessage
from fincat.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
