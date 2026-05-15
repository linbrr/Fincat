"""Chat channels module with plugin architecture."""

from fincat.channels.base import BaseChannel
from fincat.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
