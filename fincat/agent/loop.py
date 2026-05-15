"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import time
from contextlib import AsyncExitStack, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from fincat.agent.memory_wakeup_log import Timer

from fincat.agent.autocompact import AutoCompact
from fincat.agent.context import ContextBuilder
from fincat.agent.events import EventBus
from fincat.agent.skill_evolver import SkillEvolver, TaskContext
from fincat.agent.hook import AgentHook, AgentHookContext, CompositeHook
from fincat.agent.memory import Consolidator, Dream
from fincat.agent.memory_item import create_item_store
from fincat.agent.memory_monitor import AlertLevel, MemoryMonitor
from fincat.agent.memory_sqlite import SQLiteMemoryStore
from fincat.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from fincat.agent.skills import BUILTIN_SKILLS_DIR
from fincat.agent.subagent import SubagentManager
from fincat.agent.tools.cron import CronTool
from fincat.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from fincat.agent.tools.message import MessageTool
from fincat.agent.tools.notebook import NotebookEditTool
from fincat.agent.tools.registry import ToolRegistry
from fincat.agent.tools.search import GlobTool, GrepTool
from fincat.agent.tools.shell import ExecTool
from fincat.agent.tools.spawn import SpawnTool
from fincat.agent.tools.akshare import (
    StockQuoteTool,
    StockKlineTool,
    StockIntradayTool,
    StockFinancialTool,
    StockHsgtTool,
    StockBlockTool,
    StockIndicatorTool,
    StockNewsTool,
)
from fincat.agent.tools.rag import RAGSearchTool
from fincat.agent.tools.web import WebFetchTool, WebSearchTool
from fincat.bus.events import InboundMessage, OutboundMessage
from fincat.bus.queue import MessageBus
from fincat.command import CommandContext, CommandRouter, register_builtin_commands
from fincat.config.schema import AgentDefaults
from fincat.providers.base import LLMProvider
from fincat.session.manager import Session, SessionManager
from fincat.utils.document import extract_documents
from fincat.utils.helpers import image_placeholder_text
from fincat.utils.helpers import truncate_text as truncate_text_fn
from fincat.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
from fincat.eval.config import LANGFUSE_ENABLED

if TYPE_CHECKING:
    from fincat.config.schema import ChannelsConfig, ExecToolConfig, WebToolsConfig
    from fincat.cron.service import CronService


UNIFIED_SESSION_KEY = "unified:default"


class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> None:
        super().__init__(reraise=True)
        self._loop = agent_loop
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from fincat.utils.helpers import strip_think

        prev_clean = strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean) :]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._on_progress:
            if not self._on_stream:
                thought = self._loop._strip_think(
                    context.response.content if context.response else None
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = self._loop._strip_think(self._loop._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._loop._set_tool_context(self._channel, self._chat_id, self._message_id)

    async def after_iteration(self, context: AgentHookContext) -> None:
        u = context.usage or {}
        logger.debug(
            "LLM usage: prompt={} completion={} cached={}",
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            u.get("cached_tokens", 0),
        )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._loop._strip_think(content)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    _PENDING_USER_TURN_KEY = "pending_user_turn"

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        web_config: WebToolsConfig | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        session_ttl_minutes: int = 0,
        hooks: list[AgentHook] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
    ):
        from fincat.config.schema import ExecToolConfig, WebToolsConfig

        defaults = AgentDefaults()
        self._defaults = defaults
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        self.context_window_tokens = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.context_window_tokens
        )
        self.context_block_limit = context_block_limit
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.provider_retry_mode = provider_retry_mode
        self.web_config = web_config or WebToolsConfig()
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._extra_hooks: list[AgentHook] = hooks or []

        db_path = workspace / ".fincat" / "memory.db"
        enable_sqlite = os.environ.get("FINCAT_ENABLE_SQLITE_MEMORY", "0") == "1"
        self._memory_store = SQLiteMemoryStore(db_path) if enable_sqlite else None

        # Skill usage tracker for quality scoring and lifecycle management
        from fincat.agent.skill_tracker import SkillUsageTracker
        self._usage_tracker = SkillUsageTracker(workspace=workspace)

        self.context = ContextBuilder(
            workspace,
            timezone=timezone,
            disabled_skills=disabled_skills,
            memory_store=self._memory_store,
            skills_dirs=[Path(d) for d in defaults.skills_dirs] if defaults.skills_dirs else None,
            external_mutable=defaults.external_skills_mutable,
            usage_tracker=self._usage_tracker,
            config=None,
            tools_registry=None,  # Set after _register_default_tools() below
        )
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_config=self.web_config,
            max_tool_result_chars=self.max_tool_result_chars,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
        )
        self._unified_session = unified_session
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Per-session pending queues for mid-turn message injection.
        # When a session has an active task, new messages for that session
        # are routed here instead of creating a new task.
        self._pending_queues: dict[str, asyncio.Queue] = {}
        # FINCAT_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("FINCAT_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.consolidator = Consolidator(
            store=self.context.memory,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            max_completion_tokens=provider.generation.max_tokens,
        )
        self.auto_compact = AutoCompact(
            sessions=self.sessions,
            consolidator=self.consolidator,
            session_ttl_minutes=session_ttl_minutes,
        )
        item_store = create_item_store(self.workspace / "memory")
        self._item_store = item_store

        # New memory architecture: ResourceStore + PredictionEngine
        from fincat.agent.resource_store import ResourceStore
        from fincat.agent.topic import PredictionEngine
        from fincat.agent.pattern_miner import PatternMiner
        from fincat.config.paths import get_resources_dir, get_memory_db_path, get_vector_dir

        self._resource_store = ResourceStore(get_resources_dir())

        # EmbeddingEngine + MemoryStoreV2 (L2 layer)
        try:
            from fincat.agent.embedding import EmbeddingEngine
            from fincat.agent.memory_store_v2 import MemoryStoreV2
            self._embedding = EmbeddingEngine()
            self._memory_store_v2 = MemoryStoreV2(
                db_path=get_memory_db_path(),
                vector_dir=get_vector_dir(),
                embedding=self._embedding,
            )
            logger.info("EmbeddingEngine + MemoryStoreV2 initialized (dim={})", self._embedding.dimension)
        except Exception as e:
            logger.warning("EmbeddingEngine/MemoryStoreV2 unavailable, falling back to memory.md only: {}", e)
            self._embedding = None
            self._memory_store_v2 = None

        # Wire MemoryStoreV2 + Embedding into ContextBuilder
        self.context._memory_store_v2 = self._memory_store_v2
        self.context._embedding = self._embedding

        # DynamicRuleStore: persist PatternMiner-generated meta-rules
        # Must be created before PredictionEngine so it can reload dynamic rules
        from fincat.agent.dynamic_rule_store import DynamicRuleStore
        self._dynamic_rule_store = DynamicRuleStore(
            path=self.workspace / "memory" / "dynamic_rules.jsonl"
        )
        self.context._dynamic_rule_store = self._dynamic_rule_store

        self._prediction_engine = PredictionEngine(
            rules_path=Path(__file__).parent / "rules.json",
            embedding=self._embedding,
            dynamic_rule_store=self._dynamic_rule_store,
        )

        # BatchExtractor: P1/P2/P3 memory extraction from conversations
        if self._memory_store_v2:
            from fincat.agent.batch_extractor import BatchExtractor
            self._batch_extractor = BatchExtractor(
                store=self._memory_store_v2,
                provider=provider,
                model=self.model,
                resource_store=self._resource_store,
                category_manager=self.context._category_manager,
            )
        else:
            self._batch_extractor = None
        self._pattern_miner = PatternMiner(
            resource_store=self._resource_store,
            memory_db=get_memory_db_path(),
        )

        # Keep TopicStore + TopicDispatcher for backward compatibility
        from fincat.agent.topic import TopicStore
        from fincat.agent.topic import TopicDispatcher
        self._topic_store = TopicStore(self.workspace / "memory" / "topics.jsonl")
        self._topic_dispatcher = TopicDispatcher(store=self._topic_store)

        # PreFilter disabled — replaced by ResourceStore + BatchExtractor
        self._prefilter = None

        # Memory retrieval pre-filter: intent + blacklist + session cache
        from fincat.agent.memory_retrieval_filter import MemoryRetrievalFilter
        self._retrieval_filter = MemoryRetrievalFilter()

        # Query preprocessor + vector cache + category index
        from fincat.agent.query_preprocessor import QueryPreprocessor
        from fincat.agent.query_cache import QueryVectorCache
        from fincat.agent.category_vector_index import CategoryVectorIndex
        from fincat.agent.text_buffer import RecentItemBuffer
        self._query_preprocessor = QueryPreprocessor()
        self._query_cache = QueryVectorCache(max_size=1000)
        self._category_index = CategoryVectorIndex(
            embedding=self._embedding, threshold=0.55,
        ) if self._embedding else None
        self._text_buffer = RecentItemBuffer(max_items=500)

        # Build category vector index from CategoryManager metadata
        if self._category_index and self._embedding:
            try:
                cat_metas = self.context._category_manager._index.list_active()
                cat_data = [
                    {
                        "id": m["category_id"],
                        "name": m.get("name", ""),
                        "summary": m.get("summary", m.get("name", "")),
                        "item_count": 0,
                    }
                    for m in cat_metas
                ]
                if cat_data:
                    self._category_index.build(cat_data)
                    logger.info("CategoryVectorIndex built with {} categories", len(cat_data))
            except Exception as e:
                logger.warning("CategoryVectorIndex build failed: {}", e)

        # Wire CategoryManager item-change events → vector sync
        if self._memory_store_v2 and self._embedding:
            self._setup_vector_sync()

        # Memory wakeup logger: black-box recorder for memory retrieval pipeline
        from fincat.agent.memory_wakeup_log import MemoryWakeupLogger
        self._wakeup_logger = MemoryWakeupLogger(get_resources_dir())

        self.dream = Dream(
            store=self.context.memory,
            provider=provider,
            model=self.model,
            item_store=item_store,
            prefilter=None,
            category_manager=self.context._category_manager,
            resource_store=self._resource_store,
            memory_store_v2=self._memory_store_v2,
            embedding=self._embedding,
            pattern_miner=self._pattern_miner,
            dynamic_rule_store=self._dynamic_rule_store,
            prediction_engine=self._prediction_engine,
        )
        self._ensure_dream_cron_job()

        # Phase 3: Pattern snapshot + Event queue
        from fincat.agent.event_sources import EventQueue
        from fincat.agent.pattern_snapshot import PatternSnapshotStore
        self._snapshot_store = PatternSnapshotStore(self.workspace / "memory" / ".pattern_snapshots.jsonl")
        self._event_queue = EventQueue()

        # EventTopicBridge: ExternalEvent → UnifiedTopic
        from fincat.agent.topic import EventTopicBridge
        self._event_topic_bridge = EventTopicBridge(
            event_queue=self._event_queue,
            topic_store=self._topic_store,
        )

        self.monitor = MemoryMonitor(
            item_store=item_store,
            snapshot_store=self._snapshot_store,
            event_queue=self._event_queue,
            knowledge_store=getattr(self, '_knowledge_store', None),
        )
        self._ensure_pattern_cron_job()
        self._event_bus = EventBus()
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)
        self._register_default_tools()
        self._register_data_tools()

        # Initialize memory manager + skill memory provider (for cross-turn prefetch)
        from fincat.agent.memory_manager import MemoryManager
        from fincat.agent.skill_memory_provider import SkillMemoryProvider
        self._memory_manager: MemoryManager | None = None
        if enable_sqlite:
            skill_mem_provider = SkillMemoryProvider(
                workspace=workspace,
                db_path=db_path,
            )
            self._memory_manager = MemoryManager([skill_mem_provider])

        # Initialize skill evolver (Phase 1: LLM 判断值得保存 → Phase 2: skill_manage 执行保存)
        from fincat.agent.skills import SkillValidator
        from fincat.agent.tools.skill_manage import SkillManageTool
        validator = SkillValidator(tools_registry=self.tools)
        self._skill_manage = SkillManageTool(
            workspace=workspace,
            validator=validator,
            tracker=self._usage_tracker,
            memory_manager=self._memory_manager,
        )
        self._skill_evolver = SkillEvolver(
            workspace=workspace,
            provider=provider,
            model=self.model,
            skill_manage_tool=self._skill_manage,
            cron_service=self.cron_service,
            embedding=self._embedding,
        )
        # 确保 lifecycle cron job 存在
        self._skill_evolver._ensure_lifecycle_cron_job()
        # 注册 skill_manage 工具（在初始化之后）
        self.tools.register(self._skill_manage)

        # SkillRouter: 4-layer pre-retrieval for skill selection
        self._skill_router = None
        if self._embedding:
            from fincat.agent.skill_router import SkillRouter
            self._skill_router = SkillRouter(
                skills_loader=self.context.skills,
                embedding=self._embedding,
                usage_tracker=self._usage_tracker,
                query_cache=self._query_cache,
                query_preprocessor=self._query_preprocessor,
            )
            # Wire skill change callback for vector index invalidation
            self._skill_manage.on_skill_change = self._skill_router.invalidate

    def _register_default_tools(self) -> None:
        """注册框架内置的默认工具。"""
        # MessageTool: 允许 Agent 发送消息给用户
        allowed_dir = (
            self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
        )
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
            )
        )
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        for cls in (GlobTool, GrepTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                )
            )
        if self.web_config.enable:
            self.tools.register(
                WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy)
            )
            self.tools.register(WebFetchTool(proxy=self.web_config.proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )

    def _ensure_dream_cron_job(self) -> None:
        """Register a daily Dream cron job if not already present."""
        if not self.cron_service:
            return
        try:
            from fincat.cron.types import CronJob, CronPayload, CronSchedule

            jobs = self.cron_service.list_jobs()
            if any(j.name == "dream" for j in jobs):
                return  # already exists

            job = CronJob(
                id="dream",
                name="dream",
                schedule=CronSchedule(kind="cron", expr="0 3 * * *"),
                payload=CronPayload(kind="system_event"),
            )
            self.cron_service.register_system_job(job)
            logger.info("Registered Dream cron job (daily 03:00)")
        except Exception as e:
            logger.warning("Failed to register Dream cron job: {}", e)

    def _ensure_pattern_cron_job(self) -> None:
        """Register a daily pattern analysis cron job (03:30) if not present."""
        if not self.cron_service:
            return
        try:
            from fincat.cron.types import CronJob, CronPayload, CronSchedule

            jobs = self.cron_service.list_jobs()
            if any(j.name == "pattern_analysis" for j in jobs):
                return

            job = CronJob(
                id="pattern_analysis",
                name="pattern_analysis",
                schedule=CronSchedule(kind="cron", expr="30 3 * * *"),
                payload=CronPayload(kind="system_event"),
            )
            self.cron_service.register_system_job(job)
            logger.info("Registered pattern_analysis cron job (daily 03:30)")
        except Exception as e:
            logger.warning("Failed to register pattern_analysis cron job: {}", e)

        # Proactive prediction: run PatternMiner daily at 03:00 (already registered above)
        # TopicCache expiry cleanup runs in _monitor_scan on each cycle

    def _register_data_tools(self) -> None:
        """Register akshare-based stock data scraping tools."""
        self.tools.register(StockQuoteTool())
        self.tools.register(StockKlineTool())
        self.tools.register(StockIntradayTool())
        self.tools.register(StockFinancialTool())
        self.tools.register(StockHsgtTool())
        self.tools.register(StockBlockTool())
        self.tools.register(StockIndicatorTool())
        self.tools.register(StockNewsTool())
        # RAG knowledge base search (with hybrid retriever if available)
        self.tools.register(self._create_rag_tool())

    def _create_rag_tool(self) -> RAGSearchTool:
        """Create RAGSearchTool with HybridRetriever if possible, else fallback."""
        if self._embedding is None:
            return RAGSearchTool()

        try:
            from fincat.knowledge.store import get_knowledge_store
            from fincat.knowledge.vector_store import KnowledgeVectorStore
            from fincat.knowledge.hybrid_retriever import HybridRetriever
            from pathlib import Path

            store = get_knowledge_store()
            from fincat.config.paths import get_vector_dir
            vector_dir = get_vector_dir()
            vector_store = KnowledgeVectorStore(
                db_path=Path(store.db_path),
                vector_dir=vector_dir,
                embedding=self._embedding,
            )
            retriever = HybridRetriever(store, vector_store, self._embedding)
            logger.info("HybridRetriever initialized for RAG tool")
            return RAGSearchTool(hybrid_retriever=retriever, store=store)
        except Exception as e:
            logger.info("HybridRetriever unavailable, using legacy RAG search: {}", e)
            return RAGSearchTool()

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from fincat.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stacks = await connect_mcp_servers(self._mcp_servers, self.tools)
            if self._mcp_stacks:
                self._mcp_connected = True
            else:
                logger.warning("No MCP servers connected successfully (will retry next message)")
        except asyncio.CancelledError:
            logger.warning("MCP connection cancelled (will retry next message)")
            self._mcp_stacks.clear()
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            self._mcp_stacks.clear()
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from fincat.utils.helpers import strip_think

        return strip_think(text) or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hints with smart abbreviation."""
        from fincat.utils.tool_hints import format_tool_hints

        return format_tool_hints(tool_calls)

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        pending_queue: asyncio.Queue | None = None,
        pipeline_data: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
        )

        # 条件注入 LangfuseHook（环境变量控制，零侵入）
        hooks_for_run: list[AgentHook] = [loop_hook]
        if LANGFUSE_ENABLED:
            from fincat.eval.langfuse_hook import LangfuseHook
            hooks_for_run.append(LangfuseHook(
                session_id=session.key if session else None,
                user_id=chat_id,
                channel=channel,
                metadata={"message_id": message_id},
            ))
        hooks_for_run.extend(self._extra_hooks)

        hook: AgentHook = (
            CompositeHook(hooks_for_run) if len(hooks_for_run) > 1 else hooks_for_run[0]
        )

        # Trace pre-loop pipeline steps on the LangfuseHook (before any iterations)
        if pipeline_data:
            from fincat.eval.langfuse_hook import LangfuseHook
            for h in hooks_for_run:
                if isinstance(h, LangfuseHook):
                    for step in pipeline_data:
                        h.trace_pipeline_step(
                            name=step["name"],
                            input_data=step.get("input"),
                            output_data=step.get("output"),
                            metadata=step.get("metadata"),
                            duration_ms=step.get("duration_ms"),
                        )
                    break

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """Non-blocking drain of follow-up messages from the pending queue."""
            if pending_queue is None:
                return []
            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    pending_msg = pending_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = extract_documents(content, media)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                runtime_ctx = self.context._build_runtime_context(
                    pending_msg.channel,
                    pending_msg.chat_id,
                    self.context.timezone,
                )
                if isinstance(user_content, str):
                    merged: str | list[dict[str, Any]] = f"{runtime_ctx}\n\n{user_content}"
                else:
                    merged = [{"type": "text", "text": runtime_ctx}] + user_content
                items.append({"role": "user", "content": merged})
            return items

        # Defense pipeline (financial compliance)
        defense_pipeline = None
        if self._defaults.defense_enabled:
            from fincat.agent.defense.defense_pipeline import DefensePipeline
            from fincat.agent.defense.compliance_guard import ComplianceGuard
            from fincat.agent.defense.pii_scanner import PIIScanner
            from fincat.agent.defense.risk_scorer import FinancialRiskScorer
            from fincat.agent.defense.alert_manager import RiskAlertManager

            compliance_guard = ComplianceGuard(
                enable_semantic=self._defaults.defense_compliance_use_llm,
                provider=self.provider if self._defaults.defense_compliance_use_llm else None,
            )
            defense_pipeline = DefensePipeline(
                pii_scanner=PIIScanner() if self._defaults.defense_pii_scanner else None,
                compliance_guard=compliance_guard if self._defaults.defense_compliance_guard else None,
                risk_scorer=FinancialRiskScorer() if self._defaults.defense_risk_scorer else None,
                alert_manager=RiskAlertManager(),
            )

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            checkpoint_callback=_checkpoint,
            injection_callback=_drain_pending,
            defense_pipeline=defense_pipeline,
        ))
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])

        # 刷新 Langfuse 缓冲区
        if LANGFUSE_ENABLED:
            for h in hooks_for_run:
                if hasattr(h, "flush"):
                    h.flush()

        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        # Start topic dispatcher (zero-token dispatch loop)
        await self._topic_dispatcher.start()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                self.auto_compact.check_expired(
                    self._schedule_background,
                    active_session_keys=self._pending_queues.keys(),
                )
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=self)
                result = await self.commands.dispatch_priority(ctx)
                if result:
                    await self.bus.publish_outbound(result)
                continue
            effective_key = self._effective_session_key(msg)
            # If this session already has an active pending queue (i.e. a task
            # is processing this session), route the message there for mid-turn
            # injection instead of creating a competing task.
            if effective_key in self._pending_queues:
                pending_msg = msg
                if effective_key != msg.session_key:
                    pending_msg = dataclasses.replace(
                        msg,
                        session_key_override=effective_key,
                    )
                try:
                    self._pending_queues[effective_key].put_nowait(pending_msg)
                except asyncio.QueueFull:
                    logger.warning(
                        "Pending queue full for session {}, falling back to queued task",
                        effective_key,
                    )
                else:
                    logger.info(
                        "Routed follow-up message to pending queue for session {}",
                        effective_key,
                    )
                    continue
            # Compute the effective session key before dispatching
            # This ensures /stop command can find tasks correctly when unified session is enabled
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(effective_key, []).append(task)
            task.add_done_callback(
                lambda t, k=effective_key: self._active_tasks.get(k, [])
                and self._active_tasks[k].remove(t)
                if t in self._active_tasks.get(k, [])
                else None
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        session_key = self._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()

        # Register a pending queue so follow-up messages for this session are
        # routed here (mid-turn injection) instead of spawning a new task.
        pending = asyncio.Queue(maxsize=20)
        self._pending_queues[session_key] = pending

        try:
            async with lock, gate:
                try:
                    on_stream = on_stream_end = None
                    if msg.metadata.get("_wants_stream"):
                        # Split one answer into distinct stream segments.
                        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                        stream_segment = 0

                        def _current_stream_id() -> str:
                            return f"{stream_base_id}:{stream_segment}"

                        async def on_stream(delta: str) -> None:
                            meta = dict(msg.metadata or {})
                            meta["_stream_delta"] = True
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content=delta,
                                metadata=meta,
                            ))

                        async def on_stream_end(*, resuming: bool = False) -> None:
                            nonlocal stream_segment
                            meta = dict(msg.metadata or {})
                            meta["_stream_end"] = True
                            meta["_resuming"] = resuming
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="",
                                metadata=meta,
                            ))
                            stream_segment += 1

                    response = await self._process_message(
                        msg, on_stream=on_stream, on_stream_end=on_stream_end,
                        pending_queue=pending,
                    )
                    if response is not None:
                        await self.bus.publish_outbound(response)
                        # Push structured chart data via WebSocket
                        if msg.channel != "cli":
                            await self._push_chart_data(msg)
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="", metadata=msg.metadata or {},
                        ))
                except asyncio.CancelledError:
                    logger.info("Task cancelled for session {}", session_key)
                    raise
                except Exception:
                    logger.exception("Error processing message for session {}", session_key)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    ))
        finally:
            # Drain any messages still in the pending queue and re-publish
            # them to the bus so they are processed as fresh inbound messages
            # rather than silently lost.
            queue = self._pending_queues.pop(session_key, None)
            if queue is not None:
                leftover = 0
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await self.bus.publish_inbound(item)
                    leftover += 1
                if leftover:
                    logger.info(
                        "Re-published {} leftover message(s) to bus for session {}",
                        leftover, session_key,
                    )

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        await self._topic_dispatcher.stop()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        for name, stack in self._mcp_stacks.items():
            try:
                await stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
        self._mcp_stacks.clear()

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    # ------------------------------------------------------------------
    # Vector sync: CategoryManager item-change → MemoryStoreV2 + caches
    # ------------------------------------------------------------------

    def _setup_vector_sync(self) -> None:
        """Wire CategoryManager item-change events to vector sync."""
        def _on_item_change(event_type: str, payload: dict) -> None:
            self._schedule_background(
                self._handle_vector_sync(event_type, payload),
            )
        self.context._category_manager.set_on_item_change(_on_item_change)

    async def _handle_vector_sync(self, event_type: str, payload: dict) -> None:
        try:
            if event_type == "item_added":
                await self._sync_item_added(payload)
            elif event_type == "item_removed":
                await self._sync_item_removed(payload)
            elif event_type == "item_moved":
                await self._sync_item_moved(payload)
            elif event_type == "item_updated":
                await self._sync_item_updated(payload)
        except Exception:
            logger.exception("Vector sync failed for event {}", event_type)
        finally:
            self._query_cache.clear()

    async def _sync_item_added(self, payload: dict) -> None:
        item_id, category_id = payload["item_id"], payload["category_id"]
        summary = payload["item_data"].get("summary", "")
        if not summary:
            return
        # Only embed if not already embedded (BatchExtractor may have done it)
        item = self._memory_store_v2.get_item(item_id)
        if item and not item.get("embedded_at"):
            self._memory_store_v2.embed_and_index(item_id, summary)
        self._memory_store_v2.update_item(item_id, category_id=category_id)
        await self._recalc_category_vector(category_id)
        self._text_buffer.add(item_id, summary, category_id)

    async def _sync_item_removed(self, payload: dict) -> None:
        item_id, category_id = payload["item_id"], payload["category_id"]
        self._memory_store_v2.deactivate_item(item_id)
        await self._recalc_category_vector(category_id)
        self._text_buffer.remove(item_id)

    async def _sync_item_moved(self, payload: dict) -> None:
        item_id = payload["item_id"]
        self._memory_store_v2.update_item(item_id, category_id=payload["to_category_id"])
        await self._recalc_category_vector(payload["from_category_id"])
        await self._recalc_category_vector(payload["to_category_id"])
        item = self._memory_store_v2.get_item(item_id)
        if item:
            self._text_buffer.add(item_id, item.get("summary", ""), payload["to_category_id"])

    async def _sync_item_updated(self, payload: dict) -> None:
        item_id, category_id = payload["item_id"], payload["category_id"]
        new_summary = payload["new_summary"]
        self._memory_store_v2.replace_vector(item_id, new_summary)
        await self._recalc_category_vector(category_id)
        self._text_buffer.add(item_id, new_summary, category_id)

    async def _recalc_category_vector(self, category_id: str) -> None:
        """Recalculate a category's vector from its member items."""
        if not self._category_index or not self._memory_store_v2:
            return
        try:
            vec = self._memory_store_v2.compute_category_vector_from_items(category_id)
            items = self._memory_store_v2.query(category_id=category_id, is_active=True)
            item_count = len(items)
            cat_meta = self.context._category_manager._index.get(category_id)
            name = cat_meta.get("name") if cat_meta else None

            if vec is not None:
                self._category_index.update_category_with_vector(
                    category_id, vec, item_count=item_count, name=name,
                )
            elif cat_meta:
                summary = cat_meta.get("summary", cat_meta.get("name", ""))
                if summary:
                    self._category_index.update_category(
                        category_id, summary, item_count=item_count,
                    )
        except Exception:
            logger.exception("Category vector recalculation failed for {}", category_id)

    async def _poll_event_sources(self) -> None:
        """Poll event sources and push events into the queue."""
        try:
            from fincat.agent.event_sources import DocumentEventSource, MarketEventSource

            # Load entity aliases for market source
            aliases_path = self.workspace / "fincat" / "knowledge" / "entity_aliases.json"
            aliases = {}
            if aliases_path.exists():
                import json
                try:
                    data = json.loads(aliases_path.read_text(encoding="utf-8"))
                    aliases = data.get("aliases", {})
                except Exception:
                    pass

            market_src = MarketEventSource(entity_aliases=aliases)
            doc_src = DocumentEventSource(knowledge_store=None)  # knowledge_store wired later if available

            for src in [market_src, doc_src]:
                try:
                    events = await src.poll()
                    for event in events:
                        await self._event_queue.push(event)
                except Exception:
                    logger.debug("Event source poll failed: {}", type(src).__name__, exc_info=True)

            # Drain events → TopicStore for frontend dispatch
            try:
                await self._event_topic_bridge.drain()
            except Exception:
                logger.debug("EventTopicBridge drain failed", exc_info=True)
        except Exception:
            logger.debug("Event source polling failed", exc_info=True)

    async def _monitor_scan(self) -> None:
        """Run MemoryMonitor scan, publish alerts, and handle archive actions."""
        try:
            triggers = self.monitor.scan()
            for trigger in triggers[:3]:  # max 3 notifications per scan
                if trigger.alert == AlertLevel.ARCHIVE and trigger.item is not None:
                    self._item_store.archive(trigger.item.item_id)
                elif trigger.alert in (AlertLevel.REMIND, AlertLevel.CONFIRM):
                    await self.bus.publish_outbound(OutboundMessage(
                        channel="cli",
                        chat_id="direct",
                        content=f"[Monitor] {trigger.message}",
                    ))

            # Phase 3: drain event queue
            event_triggers = await self.monitor.scan_events()
            for trigger in event_triggers[:3]:
                if trigger.alert in (AlertLevel.REMIND, AlertLevel.CONFIRM):
                    await self.bus.publish_outbound(OutboundMessage(
                        channel="cli",
                        chat_id="direct",
                        content=f"[Event] {trigger.message}",
                    ))

            # Topic store: clean expired topics
            try:
                self._topic_store.cleanup()
            except Exception:
                logger.debug("TopicStore cleanup failed", exc_info=True)
        except Exception:
            logger.exception("Monitor scan failed")

    async def _push_chart_data(self, msg: InboundMessage) -> None:
        """Push structured chart data to WebSocket clients after agent completes."""
        from fincat.chart_builder import pop_chart_data
        for chart_type in ("kline", "quote", "trend"):
            structured = pop_chart_data(chart_type)
            if not structured:
                continue
            df = structured.get("df")
            if df is None:
                continue
            payload = json.dumps({
                "event": "data",
                "eventType": chart_type,
                "payload": {
                    "data": df.to_dict(orient="records"),
                    "meta": {k: v for k, v in structured.items() if k != "df"},
                },
            }, ensure_ascii=False, default=str)
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=payload,
                metadata={"_chart_data": True},
            ))

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        _msg_start_time = datetime.now(timezone.utc)
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            if self._restore_runtime_checkpoint(session):
                self.sessions.save(session)
            if self._restore_pending_user_turn(session):
                self.sessions.save(session)

            session, pending = self.auto_compact.prepare_session(session, key)

            await self.consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            current_role = "assistant" if msg.sender_id == "subagent" else "user"

            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
                session_summary=pending,
                current_role=current_role,
            )
            final_content, _, all_msgs, _, _ = await self._run_agent_loop(
                messages, session=session, channel=channel, chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
            )
            self._save_turn(session, all_msgs, 1 + len(history))
            self._clear_runtime_checkpoint(session)
            self.sessions.save(session)
            self._schedule_background(self._monitor_scan())
            self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
            )

        # Extract document text from media at the processing boundary so all
        # channels benefit without format-specific logic in ContextBuilder.
        if msg.media:
            new_content, image_only = extract_documents(msg.content, msg.media)
            msg = dataclasses.replace(msg, content=new_content, media=image_only)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        if self._restore_runtime_checkpoint(session):
            self.sessions.save(session)
        if self._restore_pending_user_turn(session):
            self.sessions.save(session)

        session, pending = self.auto_compact.prepare_session(session, key)

        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        await self.consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)

        # Memory retrieval pre-filter: skip memory.md injection for non-memory queries
        raw_text = msg.content if isinstance(msg.content, str) else str(msg.content)
        _wakeup = self._wakeup_logger.begin(query=raw_text, session_id=key)
        with Timer() as _t_filter:
            _filter_decision = self._retrieval_filter.should_retrieve(raw_text, session_key=key)
        _wakeup.filter_time_ms = _t_filter.elapsed_ms
        _wakeup.filter_skip = _filter_decision.skip
        _wakeup.filter_reason = _filter_decision.reason
        self.context._skip_memory = _filter_decision.skip

        # ---- Full retrieval pipeline (when MemoryStoreV2 available) ----
        _retrieved_items: list[dict] = []
        _pipeline_data: list[dict[str, Any]] = []
        if self._memory_store_v2 and self._embedding and not _filter_decision.skip:
            with Timer() as _t_pipeline:
                # Step 1: Query preprocessing
                processed_query = self._query_preprocessor.preprocess(raw_text)

                # Step 2: Query vector (cache → embed)
                with Timer() as _t_embed:
                    query_vec = self._query_cache.get(processed_query)
                    if query_vec is None:
                        query_vec = self._embedding.embed(processed_query)
                        self._query_cache.put(processed_query, query_vec)
                _wakeup.query_embedding_time_ms = _t_embed.elapsed_ms

                # Step 3: Category matching
                with Timer() as _t_cat:
                    category_ids: list[str] = []
                    if self._category_index and self._category_index.size > 0:
                        cat_results = self._category_index.search(query_vec, top_k=3)
                        category_ids = [c["category_id"] for c in cat_results]
                _wakeup.category_match_time_ms = _t_cat.elapsed_ms

                # Step 4: Item retrieval with ranking
                with Timer() as _t_items:
                    _retrieved_items = self._memory_store_v2.search_with_ranking(
                        query_vec=query_vec,
                        category_ids=category_ids or None,
                        top_k=5,
                        threshold=0.6,
                    )
                    # Also search text buffer (un-indexed recent items)
                    buffer_results = self._text_buffer.search(
                        raw_text, top_k=3,
                        category_ids=category_ids or None,
                    )
                    _retrieved_items.extend(buffer_results)
                _wakeup.item_retrieve_time_ms = _t_items.elapsed_ms

                # Step 5: Async metadata update (fire-and-forget)
                if _retrieved_items:
                    item_ids = [r["item_id"] for r in _retrieved_items if "item_id" in r]
                    if item_ids:
                        self._schedule_background(self._memory_store_v2.touch_items_async(item_ids))

            _wakeup.retrieval_pipeline_time_ms = _t_pipeline.elapsed_ms
            _pipeline_data.append({
                "name": "memory_retrieval",
                "input": raw_text,
                "output": {
                    "items_count": len(_retrieved_items),
                    "category_ids": category_ids,
                    "items": [{"id": r.get("item_id"), "cat": r.get("category_id"), "score": round(r.get("_final_score", 0), 3)} for r in _retrieved_items[:10]],
                },
                "metadata": {"filter_skip": _filter_decision.skip, "filter_reason": _filter_decision.reason},
                "duration_ms": _t_pipeline.elapsed_ms,
            })

        # ---- Skill Pre-Retrieval ----
        _skill_routing = None
        if self._skill_router:
            with Timer() as _t_skill:
                _skill_routing = self._skill_router.route(
                    user_message=raw_text,
                    session_key=key,
                    channel=msg.channel,
                )
            _wakeup.skill_routing_time_ms = _t_skill.elapsed_ms
            _wakeup.skill_candidates = [c.name for c in _skill_routing.candidates]
            _pipeline_data.append({
                "name": "skill_routing",
                "input": raw_text,
                "output": {
                    "candidates": [{"name": c.name, "score": round(c.score, 3), "source": c.source} for c in _skill_routing.candidates],
                },
                "metadata": {"candidates_count": len(_skill_routing.candidates)},
                "duration_ms": _t_skill.elapsed_ms,
            })

        with Timer() as _t_ctx:
            initial_messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                session_summary=pending,
                media=msg.media if msg.media else None,
                channel=msg.channel,
                chat_id=msg.chat_id,
                retrieved_items=_retrieved_items,
                skill_routing=_skill_routing,
            )
        _wakeup.context_build_time_ms = _t_ctx.elapsed_ms
        _pipeline_data.append({
            "name": "context_build",
            "output": {
                "system_prompt_len": len(initial_messages[0].get("content", "")) if initial_messages else 0,
                "messages_count": len(initial_messages),
                "has_retrieved_items": len(_retrieved_items) > 0,
                "has_skill_routing": _skill_routing is not None,
            },
            "duration_ms": _t_ctx.elapsed_ms,
        })
        # Estimate memory read time (memory.md is read inside build_messages when not skipped)
        if not _filter_decision.skip:
            _wakeup.memory_read_time_ms = max(1, _t_ctx.elapsed_ms // 3)
            memory_md = self.context._category_manager.read_memory_md()
            _wakeup.memory_chars = len(memory_md)

        # Cross-turn memory prefetch: collect relevant context from memory providers
        # before the agent loop starts (参考 hermes run_agent.py:9014)
        prefetch_context = ""
        if self._memory_manager and isinstance(msg.content, str):
            try:
                prefetch_context = self._memory_manager.prefetch_all(
                    msg.content,
                    session_id=key,
                ) or ""
            except Exception:
                pass

        # Inject prefetch context into the user message (last message in initial_messages)
        if prefetch_context and initial_messages:
            last = initial_messages[-1]
            if last.get("role") == "user":
                import copy
                last = copy.copy(last)
                original_content = last.get("content", "")
                if isinstance(original_content, str):
                    last["content"] = (
                        f"{original_content}\n\n"
                        f"---\n# Relevant Context from Previous Turns\n\n"
                        f"{prefetch_context}"
                    )
                initial_messages[-1] = last

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        # Persist the triggering user message immediately, before running the
        # agent loop. If the process is killed mid-turn (OOM, SIGKILL, self-
        # restart, etc.), the existing runtime_checkpoint preserves the
        # in-flight assistant/tool state but NOT the user message itself, so
        # the user's prompt is silently lost on recovery. Saving it up front
        # makes recovery possible from the session log alone.
        user_persisted_early = False
        if isinstance(msg.content, str) and msg.content.strip():
            session.add_message("user", msg.content)
            self._mark_pending_user_turn(session)
            self.sessions.save(session)
            user_persisted_early = True

        final_content, tools_used, all_msgs, stop_reason, had_injections = await self._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            session=session,
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            pending_queue=pending_queue,
            pipeline_data=_pipeline_data,
        )

        # Finish wakeup record with outcome data
        _wakeup.model = self.model
        _wakeup.tools_used = tools_used or []
        _wakeup.response_length = len(final_content) if final_content else 0
        # Heuristic: if memory was injected and LLM used read_file on memory files, mark used
        if not _wakeup.filter_skip and tools_used:
            memory_file_reads = any(
                t == "read_file" for t in tools_used
            )
            _wakeup.used_flag = 1 if memory_file_reads else -1
        self._wakeup_logger.finish(_wakeup)

        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Skip the already-persisted user message when saving the turn
        save_skip = 1 + len(history) + (1 if user_persisted_early else 0)
        self._save_turn(session, all_msgs, save_skip)
        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        self.sessions.save(session)
        self._schedule_background(self._monitor_scan())
        self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))

        # L1: Save conversation to ResourceStore
        try:
            raw_content = msg.content if isinstance(msg.content, str) else str(msg.content)
            resource_id = self._resource_store.add_conversation(
                content=raw_content,
                metadata={"session_id": key, "channel": msg.channel},
            )
            response_ms = int((datetime.now(timezone.utc) - _msg_start_time).total_seconds() * 1000)
            content_digest = hashlib.md5(raw_content.encode("utf-8")).hexdigest()
            self._resource_store.add_interaction_log(
                category="conversation_interaction",
                action="send_message",
                session_id=key,
                content_digest=content_digest,
                content_length=len(raw_content),
                response_time_ms=response_ms,
                associated_item_ids=[],
                extra={"channel": msg.channel, "resource_id": resource_id, "tools_used": tools_used},
            )
        except Exception:
            logger.debug("ResourceStore save failed for {}", key, exc_info=True)

        # BatchExtractor: add conversation for memory extraction
        if self._batch_extractor and self._memory_store_v2:
            try:
                self._batch_extractor.add_to_pending({
                    "resource_id": resource_id,
                    "content": raw_content,
                })
                if self._batch_extractor.should_flush():
                    self._schedule_background(self._batch_extractor.flush_pending())
            except Exception:
                logger.debug("BatchExtractor pending add failed for {}", key, exc_info=True)

        # Real-time prediction: rule matching → TopicStore (for TopicDispatcher → frontend)
        try:
            recent_history = history[-3:] if history else None
            predictions = self._prediction_engine.predict(
                msg.content if isinstance(msg.content, str) else "",
                session_history=recent_history,
            )
            if predictions:
                from fincat.agent.topic import UnifiedTopic
                for pred in predictions:
                    topic = UnifiedTopic(
                        topic_id=pred.get("topic_id", ""),
                        source=pred.get("source", "prediction_engine"),
                        source_name=pred.get("source_name", "实时预测"),
                        category=pred.get("category", "insight"),
                        title=pred.get("title", ""),
                        content=pred.get("content", ""),
                        priority=pred.get("priority", 1),
                        metadata={"confidence": pred.get("confidence", 0.5)},
                    )
                    self._topic_store.add(topic)
        except Exception:
            logger.debug("PredictionEngine failed for {}", key, exc_info=True)

        # When follow-up messages were injected mid-turn, a later natural
        # language reply may address those follow-ups and should not be
        # suppressed just because MessageTool was used earlier in the turn.
        # However, if the turn falls back to the empty-final-response
        # placeholder, suppress it when the real user-visible output already
        # came from MessageTool.
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        if on_stream is not None and stop_reason != "error":
            meta["_streamed"] = True

        # Trigger skill evolver (Phase 1: LLM 判断值得保存 → Phase 2: skill_manage 执行保存)
        if self._skill_evolver:
            if tools_used or final_content:
                logger.info(
                    "[SkillEvolver] 触发评估: tools_used={}, final_content_len={}",
                    tools_used, len(final_content) if final_content else 0,
                )
                task_ctx = TaskContext(
                    user_message=msg.content,
                    assistant_response=final_content[:500] if final_content else "",
                    tools_used=tools_used,
                    iterations=1,
                    session_key=msg.session_key,
                )
                self._schedule_background(self._skill_evolver.on_task_completed(task_ctx))
            else:
                logger.debug("[SkillEvolver] 跳过：tools_used=[], final_content=''")

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the entire runtime-context block (including any session summary).
                    # The block is bounded by _RUNTIME_CONTEXT_TAG and _RUNTIME_CONTEXT_END.
                    end_marker = ContextBuilder._RUNTIME_CONTEXT_END
                    end_pos = content.find(end_marker)
                    if end_pos >= 0:
                        after = content[end_pos + len(end_marker):].lstrip("\n")
                        if after:
                            entry["content"] = after
                        else:
                            continue
                    else:
                        # Fallback: no end marker found, strip the tag prefix
                        after_tag = content[len(ContextBuilder._RUNTIME_CONTEXT_TAG):].lstrip("\n")
                        if after_tag.strip():
                            entry["content"] = after_tag
                        else:
                            continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def _mark_pending_user_turn(self, session: Session) -> None:
        session.metadata[self._PENDING_USER_TURN_KEY] = True

    def _clear_pending_user_turn(self, session: Session) -> None:
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        return True

    def _restore_pending_user_turn(self, session: Session) -> bool:
        """Close a turn that only persisted the user message before crashing."""
        from datetime import datetime

        if not session.metadata.get(self._PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()

        self._clear_pending_user_turn(session)
        return True

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [],
        )
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )
