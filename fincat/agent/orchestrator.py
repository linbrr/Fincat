"""Master-Slave Orchestrator for multi-agent collaboration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger


class TaskType(Enum):
    """Task type classification for routing."""
    DATA_QUERY = "data_query"      # SQL queries, account data
    MARKET_DATA = "market_data"    # Quotes, news, market analysis
    QUANT_CALC = "quant_calc"      # Quantitative calculations, backtests
    GENERAL = "general"             # General conversation


@dataclass
class AgentSpec:
    """Specification for a sub-agent."""
    name: str
    task_types: list[TaskType]
    model: str | None = None
    max_iterations: int = 50
    enabled: bool = True


@dataclass
class OrchestratorConfig:
    """Configuration for the orchestrator."""
    data_agent: AgentSpec = field(default_factory=lambda: AgentSpec(
        name="data_agent",
        task_types=[TaskType.DATA_QUERY],
    ))
    market_agent: AgentSpec = field(default_factory=lambda: AgentSpec(
        name="market_agent",
        task_types=[TaskType.MARKET_DATA],
    ))
    quant_agent: AgentSpec = field(default_factory=lambda: AgentSpec(
        name="quant_agent",
        task_types=[TaskType.QUANT_CALC],
    ))
    enabled: bool = True


class Orchestrator:
    """Master Agent Orchestrator for multi-agent task routing.

    Routes tasks to specialized sub-agents based on intent classification.
    """

    def __init__(
        self,
        agents: dict[str, Any],
        config: OrchestratorConfig | None = None,
    ):
        """Initialize the orchestrator.

        Args:
            agents: Dict mapping agent_id -> AgentLoop instances
            config: Optional orchestrator configuration
        """
        self.agents = agents
        self.config = config or OrchestratorConfig()
        self._master = agents.get("master") or agents.get("main")

    def classify_intent(self, message: str) -> TaskType:
        """Classify user intent to determine which agent should handle the task.

        Args:
            message: User's message content

        Returns:
            TaskType classification
        """
        message_lower = message.lower()

        # Data query patterns
        data_keywords = ["持仓", "账户", "余额", "资产", "查询", "position", "account", "balance", "asset"]
        if any(kw in message_lower for kw in data_keywords):
            return TaskType.DATA_QUERY

        # Market data patterns
        market_keywords = ["行情", "股价", "指数", "新闻", "市场", "quote", "price", "news", "market"]
        if any(kw in message_lower for kw in market_keywords):
            return TaskType.MARKET_DATA

        # Quantitative calculation patterns
        quant_keywords = ["回测", "计算", "分析", "策略", "backtest", "strategy", "calculate", "analysis"]
        if any(kw in message_lower for kw in quant_keywords):
            return TaskType.QUANT_CALC

        return TaskType.GENERAL

    def route_task(self, task_type: TaskType) -> str | None:
        """Route task type to appropriate agent ID.

        Args:
            task_type: Classified task type

        Returns:
            Agent ID or None for master-only handling
        """
        if task_type == TaskType.DATA_QUERY:
            return "data_agent"
        elif task_type == TaskType.MARKET_DATA:
            return "market_agent"
        elif task_type == TaskType.QUANT_CALC:
            return "quant_agent"
        return None  # Master handles GENERAL and TRADE_EXEC directly

    async def execute_subagent(
        self,
        agent_id: str,
        task: str,
        session_key: str | None = None,
    ) -> str:
        """Execute a task on a specific sub-agent.

        Args:
            agent_id: ID of the sub-agent to use
            task: Task description
            session_key: Optional session key for context

        Returns:
            Agent's response content
        """
        agent = self.agents.get(agent_id)
        if not agent:
            logger.warning("Sub-agent '{}' not found", agent_id)
            return f"Error: Agent '{agent_id}' not available"

        try:
            result = await agent.process_direct(
                content=task,
                session_key=session_key or f"{agent_id}:task",
            )
            if result and result.content:
                return result.content
            return "Agent returned empty response"
        except Exception as e:
            logger.exception("Sub-agent '{}' execution failed: {}", agent_id, e)
            return f"Error executing {agent_id}: {e}"

    async def orchestrate(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Main orchestration entry point.

        Classifies intent and routes to appropriate agent(s).

        Args:
            message: User message
            context: Optional context dict

        Returns:
            Orchestrated response
        """
        if not self.config.enabled:
            # Return to master only
            return ""

        task_type = self.classify_intent(message)
        agent_id = self.route_task(task_type)

        if agent_id:
            logger.info(
                "Orchestrating: task_type={}, agent={}, message={}",
                task_type.value,
                agent_id,
                message[:50],
            )
            return await self.execute_subagent(
                agent_id=agent_id,
                task=message,
                session_key=context.get("session_key") if context else None,
            )

        # Master handles GENERAL and TRADE_EXEC
        return ""


class SubAgentPool:
    """Pool of sub-agents for parallel execution."""

    def __init__(self, agents: dict[str, Any]):
        self.agents = agents

    async def execute_parallel(
        self,
        tasks: list[tuple[str, str]],
    ) -> dict[str, str]:
        """Execute multiple tasks in parallel across different agents.

        Args:
            tasks: List of (agent_id, task) tuples

        Returns:
            Dict mapping agent_id -> response
        """
        async def execute_one(agent_id: str, task: str) -> tuple[str, str]:
            agent = self.agents.get(agent_id)
            if not agent:
                return agent_id, f"Agent '{agent_id}' not found"
            try:
                result = await agent.process_direct(content=task, session_key=f"{agent_id}:parallel")
                return agent_id, result.content if result else "Empty response"
            except Exception as e:
                return agent_id, f"Error: {e}"

        coros = [execute_one(agent_id, task) for agent_id, task in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)

        output: dict[str, str] = {}
        for result in results:
            if isinstance(result, BaseException):
                output["error"] = str(result)
            else:
                agent_id, response = result
                output[agent_id] = response
        return output

    async def gather_context(
        self,
        queries: list[str],
    ) -> dict[str, str]:
        """Gather context from multiple sources in parallel.

        Useful for collecting data from data_agent, market_agent, etc.

        Args:
            queries: List of query strings to execute

        Returns:
            Dict mapping query -> response
        """
        # Simple round-robin assignment to available agents
        agent_ids = list(self.agents.keys())
        if not agent_ids:
            return {}

        tasks = [
            (agent_ids[i % len(agent_ids)], query)
            for i, query in enumerate(queries)
        ]
        return await self.execute_parallel(tasks)
