"""Agent core module."""

from fincat.agent.context import ContextBuilder
from fincat.agent.hook import AgentHook, AgentHookContext, CompositeHook
from fincat.agent.loop import AgentLoop
from fincat.agent.memory import Dream, MemoryStore
from fincat.agent.skills import SkillsLoader
from fincat.agent.subagent import SubagentManager

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "Dream",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
