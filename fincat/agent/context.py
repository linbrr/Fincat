"""Context builder for assembling agent prompts."""

from __future__ import annotations

import base64
import mimetypes
import platform
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fincat.agent.memory import MemoryStore
from fincat.agent.category_manager import CategoryManager
from fincat.agent.skills import SkillsLoader
from fincat.eval.observe import observe
from fincat.utils.helpers import build_assistant_message, current_time_str, detect_image_mime
from fincat.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from fincat.agent.skill_router import SkillRoutingResult


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _MAX_RECENT_HISTORY = 20
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"

    def __init__(
        self,
        workspace: Path,
        timezone: str | None = None,
        disabled_skills: list[str] | None = None,
        memory_store: Any = None,
        skills_dirs: list[str] | None = None,
        external_mutable: bool = False,
        usage_tracker=None,
        config=None,
        tools_registry=None,
    ):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self._memory_store = memory_store  # SQLiteMemoryStore for financial context
        self._category_manager = CategoryManager(workspace / "memory")
        self._skip_memory = False  # Set by MemoryRetrievalFilter to skip memory.md injection
        self._memory_store_v2 = None  # Set by AgentLoop after MemoryStoreV2 init
        self._embedding = None  # Set by AgentLoop after EmbeddingEngine init
        self._dynamic_rule_store = None  # Set by AgentLoop for association rules

        from fincat.agent.skills import SkillContentInjector
        injector = SkillContentInjector(config=config) if config else None
        self.skills = SkillsLoader(
            workspace,
            disabled_skills=set(disabled_skills) if disabled_skills else None,
            skills_dirs=[Path(d) for d in skills_dirs] if skills_dirs else None,
            external_mutable=external_mutable,
            usage_tracker=usage_tracker,
            content_injector=injector,
            tools_registry=tools_registry,
        )

    @observe(name="context.build_system_prompt")
    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        retrieved_items: list[dict[str, Any]] | None = None,
        skill_routing: SkillRoutingResult | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills.

        Ordering optimized for prompt cache:
        1. Identity           (static, cache boundary)
        2. Bootstrap files    (static)
        3. Always-on skills   (static)
        4. Skills section     (static — router candidates or top-5 fallback)
        5. Memory injection   (semi-static — Dream nightly summary or vector retrieval)
        6. Financial context  (semi-static — positions, profile, tasks)
        7. Recent history     (dynamic — last 20 unprocessed entries)
        8. Association rules  (dynamic)
        """
        parts = [self._get_identity(channel=channel)]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        if skill_routing and skill_routing.candidates:
            # SkillRouter found candidates — load only those
            candidate_names = [c.name for c in skill_routing.candidates]
            candidate_content = self.skills.load_skills_for_context(candidate_names)
            candidate_summary = self._format_routing_candidates(skill_routing.candidates)
            parts.append(
                f"# Recommended Skills (auto-selected)\n\n"
                f"{candidate_summary}\n\n"
                f"## Skill Details\n\n{candidate_content}"
            )
        else:
            # Fallback: top-5 high-quality skills only (not all)
            top_skills = self.skills.get_top_quality_skills(n=5, exclude=set(always_skills))
            if top_skills:
                top_content = self.skills.load_skills_for_context(top_skills)
                if top_content:
                    parts.append(f"# Skills\n\n{top_content}")

        # Memory injection: prefer vector-retrieved items over static memory.md.
        # Placed before Financial Context — memory.md is a Dream-generated daily
        # summary of the user's recent state, updated nightly.
        if not self._skip_memory:
            if retrieved_items:
                parts.append(self._format_retrieved_items(retrieved_items))
            else:
                memory_md = self._category_manager.read_memory_md()
                if memory_md and not self._is_default_memory_template(memory_md):
                    parts.append(
                        f"# 用户记忆摘要\n\n{memory_md}\n\n"
                        "以上是用户的记忆摘要，包含画像、偏好、近期事件、行为洞察和合规规则。"
                        "如需详情，使用 read_file 读取对应 Category 文件。"
                    )

        # Add financial context if memory_store is available
        financial_context = self.build_financial_context(memory_store=self._memory_store)
        if financial_context:
            parts.append(f"# Financial Context\n\n{financial_context}")

        entries = self.memory.read_unprocessed_history(since_cursor=self.memory.get_last_dream_cursor())
        if entries:
            capped = entries[-self._MAX_RECENT_HISTORY:]
            parts.append("# Recent History\n\n" + "\n".join(
                f"- [{e['timestamp']}] {e['content']}" for e in capped
            ))

        # Association rules: entity co-occurrence context from PatternMiner
        assoc_context = self._build_association_context()
        if assoc_context:
            parts.append(assoc_context)

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, channel: str | None = None) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    @staticmethod
    def _format_routing_candidates(candidates: list) -> str:
        """Format skill candidates as a markdown table for the LLM."""
        lines = ["| # | Skill | Score | Source | Description |", "|---|-------|-------|--------|-------------|"]
        for i, c in enumerate(candidates, 1):
            lines.append(f"| {i} | **{c.name}** | {c.score:.2f} | {c.source} | {c.description} |")
        return "\n".join(lines)

    @staticmethod
    def _build_runtime_context(
        channel: str | None, chat_id: str | None, timezone: str | None = None,
        session_summary: str | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if session_summary:
            lines += ["", "[Resumed Session]", session_summary]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + ContextBuilder._RUNTIME_CONTEXT_END

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_default_memory_template(content: str) -> bool:
        """Check if memory.md is the default template (no user/Dream data added)."""
        stripped = content.strip()
        # Default template has headers + placeholder text but no actual entries
        default_markers = [
            "# Long-term Memory",
            "This file stores important information",
            "(Important facts about the user)",
            "(User preferences learned over t",
        ]
        return all(marker in stripped for marker in default_markers) and len(stripped) < 600

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        try:
            tpl = pkg_files("fincat") / "templates" / template_path
            if tpl.is_file():
                return content.strip() == tpl.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return False

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        session_summary: str | None = None,
        retrieved_items: list[dict[str, Any]] | None = None,
        skill_routing: SkillRoutingResult | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        runtime_ctx = self._build_runtime_context(channel, chat_id, self.timezone, session_summary=session_summary)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content
        messages = [
            {"role": "system", "content": self.build_system_prompt(skill_names, channel=channel, retrieved_items=retrieved_items, skill_routing=skill_routing)},
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: Any,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        ))
        return messages

    # =========================================================================
    # Financial Context (for trading agent)
    # =========================================================================

    def build_financial_context(
        self,
        memory_store: Any = None,
        symbols: list[str] | None = None,
    ) -> str:
        """Build financial context section for appending to system prompt.

        Args:
            memory_store: SQLiteMemoryStore instance for reading positions/transactions.
                          Defaults to self._memory_store if not provided.
            symbols: Optional list of symbols to include in context

        Returns:
            A string containing financial context sections, or empty string if no data.
        """
        # Use instance store if not explicitly passed
        memory_store = memory_store or self._memory_store
        if not memory_store:
            return ""

        parts = []

        # 1. Financial profile from SQLite user_profile table (category="financial")
        if hasattr(memory_store, "get_all_profile_keys"):
            profile = memory_store.get_all_profile_keys(category="financial")
            if profile:
                profile_lines = "\n".join(f"- {k}: {v}" for k, v in profile.items())
                parts.append(f"## User Financial Profile\n\n{profile_lines}")

        # 2. Current positions from SQLite
        if hasattr(memory_store, "get_all_assets"):
            assets = memory_store.get_all_assets()
            if assets:
                # Filter by symbols if provided
                if symbols:
                    assets = [a for a in assets if a.symbol in symbols]
                if assets:
                    parts.append(self._format_assets(assets))

        # 3. Active tasks from SQLite
        if hasattr(memory_store, "get_active_tasks"):
            tasks = memory_store.get_active_tasks()
            if tasks:
                parts.append(self._format_tasks(tasks))

        return "\n\n".join(parts) if parts else ""

    def _format_assets(self, assets: list) -> str:
        """Format assets list as markdown."""
        lines = ["## Current Positions"]
        for a in assets:
            value = a.quantity * a.avg_price if hasattr(a, "avg_price") else 0
            lines.append(f"- **{a.symbol}**: {a.quantity} shares @ ${a.avg_price:.2f} (${value:.2f})")
        return "\n".join(lines)

    def _format_tasks(self, tasks: list) -> str:
        """Format active tasks as markdown."""
        lines = ["## Active Tasks"]
        for t in tasks:
            progress = f"{t.progress*100:.0f}%" if hasattr(t, "progress") and t.progress else "0%"
            lines.append(f"- **{t.task_id}** ({t.task_type}): {t.status} - {progress}")
        return "\n".join(lines)

    @staticmethod
    def _format_retrieved_items(items: list[dict[str, Any]]) -> str:
        """Format vector-retrieved memory items for system prompt injection."""
        if not items:
            return ""
        lines = ["# Relevant Memory (vector-retrieved)", ""]
        for i, item in enumerate(items, 1):
            summary = item.get("summary", item.get("content", ""))
            category = item.get("category_id", "general")
            score = item.get("_final_score", item.get("_score", 0))
            source = item.get("source", "vector")
            lines.append(f"## [{i}] {category} (score: {score:.2f}, source: {source})")
            lines.append(summary)
            if item.get("tags"):
                tags = item["tags"] if isinstance(item["tags"], list) else []
                if tags:
                    lines.append(f"Tags: {', '.join(tags)}")
            lines.append("")
        lines.append(
            "以上是通过语义检索匹配到的记忆片段，与当前查询高度相关。"
            "请基于这些信息回答用户问题。"
        )
        return "\n".join(lines)

    def _build_association_context(self) -> str:
        """Build association context from DynamicRuleStore entity co-occurrence rules."""
        if not self._dynamic_rule_store:
            return ""
        rules = self._dynamic_rule_store.get_rules_by_type("associate")
        if not rules:
            return ""
        lines = ["# 关联信息", ""]
        for r in rules:
            entities = r.get("entities", [])
            if len(entities) >= 2:
                lines.append(f"- {entities[0]} 和 {entities[1]} 经常一起出现")
        return "\n".join(lines) if len(lines) > 2 else ""
