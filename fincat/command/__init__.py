"""Slash command routing and built-in handlers."""

from fincat.command.builtin import register_builtin_commands
from fincat.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
