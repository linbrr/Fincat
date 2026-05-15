"""Memory system: pure file I/O store, lightweight Consolidator, and Dream processor."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from fincat.utils.prompt_templates import render_template
from fincat.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain, strip_think

from fincat.agent.runner import AgentRunSpec, AgentRunner
from fincat.agent.tools.registry import ToolRegistry
from fincat.utils.gitstore import GitStore

if TYPE_CHECKING:
    from fincat.providers.base import LLMProvider
    from fincat.session.manager import Session, SessionManager


# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------

class MemoryStore:
    """Pure file I/O for memory files: category Markdown files, MEMORY.md, history.jsonl, SOUL.md, USER.md."""

    _DEFAULT_MAX_HISTORY = 1000
    _LEGACY_ENTRY_START_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[^\]]*)\]\s*")
    _LEGACY_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*")
    _LEGACY_RAW_MESSAGE_RE = re.compile(
        r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s+[A-Z][A-Z0-9_]*(?:\s+\[tools:\s*[^\]]+\])?:"
    )

    # Category Markdown files (expression layer, human-readable)
    CATEGORY_FILES = {
        "preference": "user_preferences.md",
        "knowledge": "product_knowledge.md",
        "case": "conversation_cases.md",
        "compliance": "compliance_rules.md",
        "profile": "user_profile.md",
        "insight": "behavioral_insights.md",
        "behavior": "behavior_habits.md",
    }

    _DEFAULT_CATEGORY_HEADERS = {
        "preference": "# User Preferences\n\n",
        "knowledge": "# Product Knowledge\n\n",
        "case": "# Conversation Cases\n\n",
        "compliance": "# Compliance Rules\n\n",
        "profile": "# User Profile\n\n",
        "insight": "# Behavioral Insights\n\n",
        "behavior": "# Behavior Habits\n\n",
    }

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self.memory_dir = ensure_dir(workspace / "memory")
        self.history_file = self.memory_dir / "history.jsonl"
        self.legacy_history_file = self.memory_dir / "HISTORY.md"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"

        # Init category files with default headers if they don't exist
        self._init_category_files()

        tracked = ["SOUL.md", "USER.md", "memory/memory.md"]
        tracked += [f"memory/{f}" for f in self.CATEGORY_FILES.values()]
        self._git = GitStore(workspace, tracked_files=tracked)
        self._maybe_migrate_legacy_history()

    def _init_category_files(self) -> None:
        """Create category Markdown files with default headers if missing."""
        for category, filename in self.CATEGORY_FILES.items():
            path = self.memory_dir / filename
            if not path.exists():
                header = self._DEFAULT_CATEGORY_HEADERS.get(category, "")
                path.write_text(header, encoding="utf-8")

    @property
    def git(self) -> GitStore:
        return self._git

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _maybe_migrate_legacy_history(self) -> None:
        """One-time upgrade from legacy HISTORY.md to history.jsonl.

        The migration is best-effort and prioritizes preserving as much content
        as possible over perfect parsing.
        """
        if not self.legacy_history_file.exists():
            return
        if self.history_file.exists() and self.history_file.stat().st_size > 0:
            return

        try:
            legacy_text = self.legacy_history_file.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            logger.exception("Failed to read legacy HISTORY.md for migration")
            return

        entries = self._parse_legacy_history(legacy_text)
        try:
            if entries:
                self._write_entries(entries)
                last_cursor = entries[-1]["cursor"]
                self._cursor_file.write_text(str(last_cursor), encoding="utf-8")
                # Default to "already processed" so upgrades do not replay the
                # user's entire historical archive into Dream on first start.
                self._dream_cursor_file.write_text(str(last_cursor), encoding="utf-8")

            backup_path = self._next_legacy_backup_path()
            self.legacy_history_file.replace(backup_path)
            logger.info(
                "Migrated legacy HISTORY.md to history.jsonl ({} entries)",
                len(entries),
            )
        except Exception:
            logger.exception("Failed to migrate legacy HISTORY.md")

    def _parse_legacy_history(self, text: str) -> list[dict[str, Any]]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []

        fallback_timestamp = self._legacy_fallback_timestamp()
        entries: list[dict[str, Any]] = []
        chunks = self._split_legacy_history_chunks(normalized)

        for cursor, chunk in enumerate(chunks, start=1):
            timestamp = fallback_timestamp
            content = chunk
            match = self._LEGACY_TIMESTAMP_RE.match(chunk)
            if match:
                timestamp = match.group(1)
                remainder = chunk[match.end():].lstrip()
                if remainder:
                    content = remainder

            entries.append({
                "cursor": cursor,
                "timestamp": timestamp,
                "content": content,
            })
        return entries

    def _split_legacy_history_chunks(self, text: str) -> list[str]:
        lines = text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        saw_blank_separator = False

        for line in lines:
            if saw_blank_separator and line.strip() and current:
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            if self._should_start_new_legacy_chunk(line, current):
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            current.append(line)
            saw_blank_separator = not line.strip()

        if current:
            chunks.append("\n".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    def _should_start_new_legacy_chunk(self, line: str, current: list[str]) -> bool:
        if not current:
            return False
        if not self._LEGACY_ENTRY_START_RE.match(line):
            return False
        if self._is_raw_legacy_chunk(current) and self._LEGACY_RAW_MESSAGE_RE.match(line):
            return False
        return True

    def _is_raw_legacy_chunk(self, lines: list[str]) -> bool:
        first_nonempty = next((line for line in lines if line.strip()), "")
        match = self._LEGACY_TIMESTAMP_RE.match(first_nonempty)
        if not match:
            return False
        return first_nonempty[match.end():].lstrip().startswith("[RAW]")

    def _legacy_fallback_timestamp(self) -> str:
        try:
            return datetime.fromtimestamp(
                self.legacy_history_file.stat().st_mtime,
            ).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _next_legacy_backup_path(self) -> Path:
        candidate = self.memory_dir / "HISTORY.md.bak"
        suffix = 2
        while candidate.exists():
            candidate = self.memory_dir / f"HISTORY.md.bak.{suffix}"
            suffix += 1
        return candidate

    # -- Category Markdown files ----------------------------------------------

    def category_path(self, category: str) -> Path:
        """Return the file path for a given memory category."""
        filename = self.CATEGORY_FILES.get(category)
        if not filename:
            raise ValueError(f"Unknown category: {category}. Valid: {list(self.CATEGORY_FILES)}")
        return self.memory_dir / filename

    def read_category(self, category: str) -> str:
        return self.read_file(self.category_path(category))

    def write_category(self, category: str, content: str) -> None:
        self.category_path(category).write_text(content, encoding="utf-8")

    def append_category_entry(
        self,
        category: str,
        content: str,
        *,
        item_id: str = "",
        confidence: float = 0.5,
        frequency: int = 1,
        decay_score: float = 1.0,
        timestamp: str | None = None,
    ) -> None:
        """Append a memory entry to a category Markdown file with HTML comment metadata.

        Format:
        <!-- item:{id} | ts:{iso} | freq:{n} | decay:{d} | conf:{c} -->
        - entry content
        """
        from datetime import datetime, timezone

        ts = timestamp or datetime.now(timezone.utc).isoformat()
        meta_parts = []
        if item_id:
            meta_parts.append(f"item:{item_id}")
        meta_parts.append(f"ts:{ts}")
        meta_parts.append(f"freq:{frequency}")
        meta_parts.append(f"decay:{decay_score:.2f}")
        meta_parts.append(f"conf:{confidence:.2f}")
        meta_line = f"<!-- {' | '.join(meta_parts)} -->\n"

        path = self.category_path(category)
        current = self.read_category(category)
        entry = f"{meta_line}- {content}\n\n"
        new_content = current.rstrip("\n") + "\n" + entry
        # Ensure newline at end
        if not new_content.endswith("\n"):
            new_content += "\n"
        path.write_text(new_content, encoding="utf-8")

    def parse_category_metadata(self, category: str) -> list[dict[str, Any]]:
        """Extract MemoryItem metadata from HTML comments in a category file.

        Returns list of {item_id, timestamp, frequency, decay_score, confidence, content}.
        """
        content = self.read_category(category)
        pattern = re.compile(
            r"<!--\s*item:(?P<item_id>\S+)\s*\|\s*ts:(?P<ts>[^|]+?)\s*\|\s*"
            r"freq:(?P<freq>[\d.]+)\s*\|\s*decay:(?P<decay>[\d.]+)\s*\|\s*"
            r"conf:(?P<conf>[\d.]+)\s*-->\n-\s*(?P<content>.+)",
            re.MULTILINE,
        )
        results: list[dict[str, Any]] = []
        for m in pattern.finditer(content):
            results.append({
                "item_id": m.group("item_id"),
                "timestamp": m.group("ts").strip(),
                "frequency": int(float(m.group("freq"))),
                "decay_score": float(m.group("decay")),
                "confidence": float(m.group("conf")),
                "content": m.group("content").strip(),
                "category": category,
            })
        return results

    # -- SOUL.md -------------------------------------------------------------

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    # -- USER.md -------------------------------------------------------------

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self, *, categories: list[str] | None = None) -> str:
        """Aggregate category Markdown files for context injection.

        Args:
            categories: Specific categories to include (None = all).

        Returns formatted markdown string with section per category.
        """
        cats = categories or list(self.CATEGORY_FILES)
        parts = []
        for cat in cats:
            content = self.read_category(cat)
            if content.strip():
                parts.append(content)
        return "\n\n".join(parts) if parts else ""

    def get_category_summary(self, max_lines: int = 5) -> str:
        """Return a short summary of each category (first N lines each)."""
        lines = []
        labels = {
            "preference": "User Preferences",
            "knowledge": "Product Knowledge",
            "case": "Conversation Cases",
            "compliance": "Compliance Rules",
        }
        for cat in self.CATEGORY_FILES:
            content = self.read_category(cat)
            if not content.strip():
                continue
            content_lines = content.strip().split("\n")
            body = [l for l in content_lines if not l.startswith("# ")]
            snippet = "\n".join(body[:max_lines])
            if snippet.strip():
                lines.append(f"## {labels.get(cat, cat)}\n{snippet}")
        return "\n\n".join(lines) if lines else ""

    def get_entity_summary(self) -> str:
        """Return a compact entity-focused summary for context injection."""
        import re as _re

        summaries: list[str] = []
        for cat in self.CATEGORY_FILES:
            content = self.read_category(cat)
            if not content.strip():
                continue

            entries: list[str] = []
            entry_pattern = _re.compile(
                r"<!--.*?-->\n-\s*(.+)", _re.MULTILINE
            )
            for m in entry_pattern.finditer(content):
                entry = m.group(1).strip()
                if entry:
                    entries.append(entry)

            if not entries:
                continue

            label = cat
            compact = ", ".join(entries[:5])
            if len(compact) > 150:
                compact = compact[:150] + "..."
            if len(entries) > 5:
                compact += f" (+{len(entries) - 5} more)"
            summaries.append(f"- {label}: {compact}")

        return "\n".join(summaries) if summaries else ""

    def all_categories(self) -> dict[str, str]:
        """Return {category: content} for all category files."""
        return {cat: self.read_category(cat) for cat in self.CATEGORY_FILES}

    # -- history.jsonl — append-only, JSONL format ---------------------------

    def append_history(self, entry: str) -> int:
        """Append *entry* to history.jsonl and return its auto-incrementing cursor."""
        cursor = self._next_cursor()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        record = {"cursor": cursor, "timestamp": ts, "content": strip_think(entry.rstrip()) or entry.rstrip()}
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._cursor_file.write_text(str(cursor), encoding="utf-8")
        return cursor

    def _next_cursor(self) -> int:
        """Read the current cursor counter and return next value."""
        if self._cursor_file.exists():
            try:
                return int(self._cursor_file.read_text(encoding="utf-8").strip()) + 1
            except (ValueError, OSError):
                pass
        # Fallback: read last line's cursor from the JSONL file.
        last = self._read_last_entry()
        if last:
            return last["cursor"] + 1
        return 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """Return history entries with cursor > *since_cursor*."""
        return [e for e in self._read_entries() if e["cursor"] > since_cursor]

    def compact_history(self) -> None:
        """Drop oldest entries if the file exceeds *max_history_entries*."""
        if self.max_history_entries <= 0:
            return
        entries = self._read_entries()
        if len(entries) <= self.max_history_entries:
            return
        kept = entries[-self.max_history_entries:]
        self._write_entries(kept)

    # -- JSONL helpers -------------------------------------------------------

    def _read_entries(self) -> list[dict[str, Any]]:
        """Read all entries from history.jsonl."""
        entries: list[dict[str, Any]] = []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except FileNotFoundError:
            pass
        return entries

    def _read_last_entry(self) -> dict[str, Any] | None:
        """Read the last entry from the JSONL file efficiently."""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [l for l in data.split("\n") if l.strip()]
                if not lines:
                    return None
                return json.loads(lines[-1])
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite history.jsonl with the given entries."""
        with open(self.history_file, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int:
        if self._dream_cursor_file.exists():
            try:
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pass
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self._dream_cursor_file.write_text(str(cursor), encoding="utf-8")

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    def raw_archive(self, messages: list[dict]) -> None:
        """Fallback: dump raw messages to history.jsonl without LLM summarization."""
        self.append_history(
            f"[RAW] {len(messages)} messages\n"
            f"{self._format_messages(messages)}"
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )



# ---------------------------------------------------------------------------
# Consolidator — lightweight token-budget triggered consolidation
# ---------------------------------------------------------------------------


class Consolidator:
    """Lightweight consolidation: summarizes evicted messages into history.jsonl."""

    _MAX_CONSOLIDATION_ROUNDS = 5
    _MAX_CHUNK_MESSAGES = 60  # hard cap per consolidation round

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        max_completion_tokens: int = 4096,
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = max_completion_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def _cap_consolidation_boundary(
        self,
        session: Session,
        end_idx: int,
    ) -> int | None:
        """Clamp the chunk size without breaking the user-turn boundary."""
        start = session.last_consolidated
        if end_idx - start <= self._MAX_CHUNK_MESSAGES:
            return end_idx

        capped_end = start + self._MAX_CHUNK_MESSAGES
        for idx in range(capped_end, start, -1):
            if session.messages[idx].get("role") == "user":
                return idx
        return None

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        """Estimate current prompt size for the normal session history view."""
        history = session.get_history(max_messages=0)
        channel, chat_id = (session.key.split(":", 1) if ":" in session.key else (None, None))
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive(self, messages: list[dict]) -> str | None:
        """Summarize messages via LLM and append to history.jsonl.

        Returns the summary text on success, None if nothing to archive.
        """
        if not messages:
            return None
        try:
            formatted = MemoryStore._format_messages(messages)
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template(
                            "agent/consolidator_archive.md",
                            strip=True,
                        ),
                    },
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
            )
            summary = response.content or "[no summary]"
            self.store.append_history(summary)
            return summary
        except Exception:
            logger.warning("Consolidation LLM call failed, raw-dumping to history")
            self.store.raw_archive(messages)
            return None

    # 触发条件
    async def maybe_consolidate_by_tokens(self, session: Session) -> None:
        """Loop: archive old messages until prompt fits within safe budget.

        The budget reserves space for completion tokens and a safety buffer
        so the LLM request never exceeds the context window.
        """
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            budget = self.context_window_tokens - self.max_completion_tokens - self._SAFETY_BUFFER
            target = budget // 2
            try:
                estimated, source = self.estimate_session_prompt_tokens(session)
            except Exception:
                logger.exception("Token estimation failed for {}", session.key)
                estimated, source = 0, "error"
            if estimated <= 0:
                return
            if estimated < budget:
                unconsolidated_count = len(session.messages) - session.last_consolidated
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}, msgs={}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    unconsolidated_count,
                )
                return

            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    return

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                end_idx = boundary[0]
                end_idx = self._cap_consolidation_boundary(session, end_idx)
                if end_idx is None:
                    logger.debug(
                        "Token consolidation: no capped boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    return

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                if not await self.archive(chunk):
                    return
                session.last_consolidated = end_idx
                self.sessions.save(session)

                try:
                    estimated, source = self.estimate_session_prompt_tokens(session)
                except Exception:
                    logger.exception("Token estimation failed for {}", session.key)
                    estimated, source = 0, "error"
                if estimated <= 0:
                    return


# ---------------------------------------------------------------------------
# Dream — heavyweight cron-scheduled memory consolidation
# ---------------------------------------------------------------------------


class Dream:
    """Two-phase memory processor: analyze history.jsonl, then edit category files.

    Phase 1 produces an analysis summary + extracts atomic MemoryItems.
    Phase 2 delegates to AgentRunner with read_file / edit_file tools so the
    LLM can make targeted, incremental edits to category Markdown files.

    Category-based memory replaces the old single-file MEMORY.md approach:
      - memory/user_preferences.md
      - memory/product_knowledge.md
      - memory/conversation_cases.md
      - memory/compliance_rules.md
      - memory/user_profile.md
      - memory/behavioral_insights.md
      - memory/behavior_habits.md
    """

    _FILE_RE = re.compile(r"^\[FILE\]\s+(\w+):\s*(.+)$", re.MULTILINE)
    _BEHAVIOR_RE = re.compile(r"^\[BEHAVIOR\]\s+(.+)$", re.MULTILINE)

    # Category mapping: old MemoryStore category → (CategoryManager type, builtin folder)
    # type must match _BUILTIN_FOLDERS for _resolve_folder to pick the right directory
    _CATEGORY_MAP = {
        "preference": ("preferences", "preferences"),
        "knowledge": ("knowledge", "knowledge"),
        "profile": ("profile", "profile"),
        "compliance": ("compliance", "compliance"),
        "case": ("custom", "custom"),
        "insight": ("behavioral_insights", "behavioral_insights"),
        "behavior": ("behavioral_insights", "behavioral_insights"),
    }

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_batch_size: int = 20,
        max_iterations: int = 10,
        max_tool_result_chars: int = 16_000,
        item_store: Any = None,  # MemoryItemStore, optional
        prefilter: Any = None,  # RealTimePreFilter, optional
        category_manager: Any = None,  # CategoryManager, optional
        resource_store: Any = None,  # ResourceStore, optional
        memory_store_v2: Any = None,  # MemoryStoreV2, optional (L2 vector layer)
        embedding: Any = None,  # EmbeddingEngine, optional
        conflict_detector: Any = None,  # ConflictDetector, optional
        pattern_miner: Any = None,  # PatternMiner, optional
        dynamic_rule_store: Any = None,  # DynamicRuleStore, optional
        prediction_engine: Any = None,  # PredictionEngine, optional
    ):
        self.store = store
        self.provider = provider
        self.model = model
        self.max_batch_size = max_batch_size
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self._item_store = item_store
        self._prefilter = prefilter
        self._category_manager = category_manager
        self._resource_store = resource_store
        self._memory_store_v2 = memory_store_v2
        self._embedding = embedding
        self._conflict_detector = conflict_detector
        self._pattern_miner = pattern_miner
        self._dynamic_rule_store = dynamic_rule_store
        self._prediction_engine = prediction_engine
        self._runner = AgentRunner(provider)
        self._tools = self._build_tools()

    # -- tool registry -------------------------------------------------------

    def _build_tools(self) -> ToolRegistry:
        """Build a minimal tool registry for the Dream agent."""
        from fincat.agent.skills import BUILTIN_SKILLS_DIR
        from fincat.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool

        tools = ToolRegistry()
        workspace = self.store.workspace
        # Allow reading builtin skills for reference during skill creation
        extra_read = [BUILTIN_SKILLS_DIR] if BUILTIN_SKILLS_DIR.exists() else None
        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_allowed_dirs=extra_read,
        ))
        tools.register(EditFileTool(workspace=workspace, allowed_dir=workspace))
        # write_file resolves relative paths from workspace root, but can only
        # write under skills/ so the prompt can safely use skills/<name>/SKILL.md.
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        tools.register(WriteFileTool(workspace=workspace, allowed_dir=skills_dir))
        return tools

    # -- skill listing --------------------------------------------------------

    def _list_existing_skills(self) -> list[str]:
        """List existing skills as 'name — description [usage]' for dedup context."""
        from collections import Counter

        from fincat.agent.skills import BUILTIN_SKILLS_DIR

        _DESC_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE | re.IGNORECASE)

        # Count invocations from usage file
        usage_counts: Counter[str] = Counter()
        usage_file = self.store.workspace / "memory" / "skill_usage.jsonl"
        if usage_file.exists():
            try:
                import json
                for line in usage_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        entry = json.loads(line)
                        usage_counts[entry.get("skill_name", "")] += 1
            except Exception:
                pass

        entries: dict[str, str] = {}
        for base in (self.store.workspace / "skills", BUILTIN_SKILLS_DIR):
            if not base.exists():
                continue
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.exists():
                    continue
                # Prefer workspace skills over builtin (same name)
                if d.name in entries and base == BUILTIN_SKILLS_DIR:
                    continue
                content = skill_md.read_text(encoding="utf-8")[:500]
                m = _DESC_RE.search(content)
                desc = m.group(1).strip() if m else "(no description)"
                count = usage_counts.get(d.name, 0)
                entries[d.name] = f"{desc} [调用{count}次]"
        return [f"{name} — {info}" for name, info in sorted(entries.items())]

    # -- skill evolution coordination ----------------------------------------

    def _read_recent_skill_evolutions(self, hours: int = 24) -> list[dict]:
        """Read recent skill_evolution.jsonl entries to avoid duplicate creation."""
        evolution_file = self.store.workspace / "memory" / "skill_evolution.jsonl"
        if not evolution_file.exists():
            return []

        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        recent: list[dict] = []
        try:
            with open(evolution_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", 0)
                        if isinstance(ts, str):
                            dt = datetime.fromisoformat(ts)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            ts = dt.timestamp()
                        if ts >= cutoff:
                            recent.append(entry)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            logger.debug("Could not read skill_evolution.jsonl")
        return recent

    # -- skill validation after Dream Phase 2 --------------------------------

    def _validate_dream_skills(self, result: Any) -> None:
        """Validate SKILL.md files created/modified by Dream Phase 2."""
        from fincat.agent.skills import SkillValidator

        if not result or not result.tool_events:
            return

        validator = SkillValidator(tools_registry=self._tools)
        workspace = self.store.workspace

        for event in result.tool_events:
            if event.get("status") != "ok":
                continue
            name = event.get("name", "")
            if name not in ("write_file", "edit_file"):
                continue
            detail = event.get("detail", "")
            # Extract file path from detail (e.g., "skills/foo/SKILL.md written")
            if "SKILL.md" not in detail:
                continue
            # Try to resolve the path
            skill_path = None
            for part in detail.split():
                if "SKILL.md" in part:
                    candidate = workspace / part
                    if candidate.exists():
                        skill_path = candidate
                        break
            if not skill_path:
                continue
            try:
                content = skill_path.read_text(encoding="utf-8")
                is_valid, errors = validator.validate(content)
                if is_valid:
                    logger.info("Dream skill validation passed: {}", skill_path.relative_to(workspace))
                else:
                    logger.warning(
                        "Dream skill validation failed: {} — errors: {}",
                        skill_path.relative_to(workspace), errors,
                    )
            except Exception:
                logger.exception("Dream skill validation error for {}", skill_path)

    # -- MemoryItem extraction -----------------------------------------------

    async def _extract_memory_items_from_analysis(self, analysis: str, cursor: int) -> None:
        """Parse [FILE] category: content and [BEHAVIOR] lines from Phase 1."""
        if not self._item_store:
            return
        session_key = f"dream:{cursor}"
        count = 0
        for m in self._FILE_RE.finditer(analysis):
            category = m.group(1).strip().lower()
            content = m.group(2).strip()
            if not content:
                continue
            try:
                item, is_new = await self._add_memory_item_with_conflict_check(
                    content=content,
                    category=category,
                    source_session=session_key,
                    source_round=cursor,
                )
                if item:
                    count += 1
                    if is_new:
                        # Only write to CategoryManager and MemoryStoreV2 for new items
                        self._write_to_category_manager(category, item.item_id, content)
                        self._write_to_memory_store_v2(category, content)
            except Exception:
                logger.exception("Failed to add MemoryItem for category={}", category)

        # Extract [BEHAVIOR] lines → MemoryItem(category="behavior")
        for m in self._BEHAVIOR_RE.finditer(analysis):
            content = m.group(1).strip()
            if not content:
                continue
            try:
                item, is_new = await self._add_memory_item_with_conflict_check(
                    content=content,
                    category="behavior",
                    source_session=session_key,
                    source_round=cursor,
                )
                if item:
                    count += 1
                    if is_new:
                        # Only write to CategoryManager and MemoryStoreV2 for new items
                        self._write_to_category_manager("behavior", item.item_id, content)
                        self._write_to_memory_store_v2("behavior", content)
            except Exception:
                logger.exception("Failed to add behavior MemoryItem")

        if count:
            logger.info("Dream: extracted {} MemoryItems from Phase 1", count)

    async def _add_memory_item_with_conflict_check(
        self,
        content: str,
        category: str,
        source_session: str = "",
        source_round: int = 0,
    ) -> tuple[Any, bool]:
        """添加记忆，支持冲突检测。返回 (item, is_new)。

        is_new=True 表示新增了条目，需要写 CategoryManager 和 MemoryStoreV2。
        is_new=False 表示合并到已有条目（frequency++），不需要重复写入。
        """
        before_count = len(self._item_store) if self._item_store else 0

        if self._conflict_detector and self._memory_store_v2:
            result = await self._item_store.add_with_conflict_check(
                content=content,
                category=category,
                conflict_detector=self._conflict_detector,
                store_v2=self._memory_store_v2,
                source_type="system",  # Dream 提取的记忆来源为 system
                source_session=source_session,
                source_round=source_round,
            )
            # add_with_conflict_check 可能返回列表（保留双版本时）
            if isinstance(result, list):
                item = result[0] if result else None
            else:
                item = result
        else:
            # 原有逻辑
            item = self._item_store.add(
                content=content,
                category=category,
                source_session=source_session,
                source_round=source_round,
            )

        after_count = len(self._item_store) if self._item_store else 0
        is_new = after_count > before_count
        return item, is_new

    def _write_to_category_manager(self, old_category: str, item_id: str, content: str) -> None:
        """Write an extracted item to CategoryManager directory structure."""
        if not self._category_manager:
            return
        mapping = self._CATEGORY_MAP.get(old_category)
        if not mapping:
            self._write_to_catch_all(old_category, item_id, content)
            return
        cat_type, folder = mapping
        # Find or create a category in the target folder
        cat_name = {
            "preferences": "用户偏好",
            "knowledge": "产品知识",
            "profile": "用户画像",
            "compliance": "合规规则",
            "custom": "对话案例",
            "behavioral_insights": "行为洞察",
        }.get(folder, folder)
        try:
            cat_id = self._category_manager.get_or_create_category(
                name=cat_name, type=cat_type,
            )
            self._category_manager.add_item_to_category(
                cat_id, item_id,
                {"memory_type": cat_type, "summary": content[:80]},
            )
        except Exception:
            logger.exception("Failed to write to CategoryManager: category={}", old_category)

    def _write_to_catch_all(self, category_tag: str, item_id: str, content: str) -> None:
        """Write unknown-category item to custom/misc.md with original tag."""
        if not self._category_manager:
            return
        try:
            cat_id = self._category_manager.get_or_create_category(
                name="misc", type="custom",
            )
            tagged_content = f"[{category_tag}] {content}"
            self._category_manager.add_item_to_category(
                cat_id, item_id,
                {"memory_type": "custom", "summary": tagged_content[:80]},
            )
        except Exception:
            logger.exception("Failed to write to catch-all: tag={}", category_tag)

    def _write_to_memory_store_v2(
        self, category: str, content: str, resource_id: str = "",
    ) -> None:
        """Dual-write extracted item to MemoryStoreV2 (L2 vector layer)."""
        if not self._memory_store_v2 or not self._embedding:
            return
        mapping = self._CATEGORY_MAP.get(category)
        memory_type = mapping[0] if mapping else "fact"

        summary = content[:200]

        # Dedup: same check as BatchExtractor._dedup_and_save
        similar = self._memory_store_v2.search_similar(summary, threshold=0.9)
        if similar:
            self._memory_store_v2.touch_item(similar["item_id"])
            logger.debug(
                "Dream: dedup hit for '{}' → existing item {}",
                summary[:40], similar["item_id"],
            )
            return

        # Resolve actual cate_xxx ID from CategoryManager (not folder name)
        category_id = None
        if self._category_manager:
            try:
                category_id = self._category_manager.find_best_category(
                    summary, memory_type,
                )
            except Exception:
                pass

        try:
            item_id = self._memory_store_v2.add_item(
                resource_id=resource_id,
                memory_type=memory_type,
                summary=summary,
                content=content,
                category_id=category_id,
                importance_score=0.6,
            )
            self._memory_store_v2.embed_and_index(item_id, summary)
            logger.debug("Dream: dual-wrote item {} to MemoryStoreV2", item_id)
        except Exception:
            logger.exception("Dream: MemoryStoreV2 dual-write failed for category={}", category)

    async def _extract_memory_items_llm_fallback(self, analysis: str, cursor: int) -> int:
        """When Phase 1 analysis is prose rather than tagged lines, use a focused
        LLM call to extract MemoryItems in structured JSON format."""
        if not self._item_store:
            return 0

        valid_cats = list(self.store.CATEGORY_FILES)
        prompt = f"""Extract atomic memory facts from this analysis. Output ONLY a JSON array.

Analysis:
{analysis[:3000]}

Return JSON array of objects:
[
  {{"category": "{valid_cats[0]}"|...|"{valid_cats[-1]}", "content": "atomic fact in Chinese"}}
]

Rules:
- One fact per object, keep content under 80 chars
- Skip stale/removal entries, only extract new facts
- Return [] if no facts to extract
- Output ONLY the JSON array, no markdown, no explanation"""

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                tool_choice=None,
            )
            raw = response.content.strip() if response.content else ""
            # Strip markdown code fences
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            items = json.loads(raw)
            if not isinstance(items, list):
                return 0

            session_key = f"dream:{cursor}"
            count = 0
            for obj in items:
                cat = obj.get("category", "").lower()
                content = obj.get("content", "")
                if cat not in self.store.CATEGORY_FILES or not content:
                    continue
                item, is_new = await self._add_memory_item_with_conflict_check(
                    content=content,
                    category=cat,
                    source_session=session_key,
                    source_round=cursor,
                )
                if item:
                    count += 1
                    if is_new:
                        self._write_to_category_manager(cat, item.item_id, content)
                        # Dual-write to MemoryStoreV2 (L2 vector layer)
                        self._write_to_memory_store_v2(cat, content)
            if count:
                logger.info("Dream: extracted {} MemoryItems via LLM fallback", count)
            return count
        except Exception as e:
            logger.warning("Dream: LLM fallback extraction failed: {}", e)
            return 0

    # -- Phase 3: memory.md LLM summarization --------------------------------

    async def _regenerate_memory_md_with_llm(self, changelog: list[str] | None = None) -> None:
        """Generate or update memory.md.

        First run: reads full category files to create initial summary.
        Subsequent runs: uses existing memory.md + Dream changelog for incremental update.
        """
        if not self._category_manager:
            return

        memory_md_path = self._category_manager._memory_dir / "memory.md"
        existing = ""
        if memory_md_path.exists():
            existing = memory_md_path.read_text(encoding="utf-8")

        if existing:
            # Subsequent run: existing summary + changelog → incremental update
            changelog_text = "\n".join(f"- {c}" for c in changelog) if changelog else "(无变更)"
            prompt = render_template(
                "agent/dream_phase3_memory.md",
                strip=True,
                mode="update",
                existing_summary=existing,
                changelog=changelog_text,
                category_content="",
            )
        else:
            # First run: read full category files to create initial memory.md
            category_parts: list[str] = []
            for meta in self._category_manager._index.list_active():
                cat_id = meta.get("category_id", "")
                name = meta.get("name", "")
                cat_type = meta.get("type", "custom")
                content = self._category_manager.read_category_md(cat_id) or "(empty)"
                category_parts.append(f"### [{cat_type}] {name}\n{content}")
            category_content = "\n\n---\n\n".join(category_parts)
            if not category_content.strip():
                return
            prompt = render_template(
                "agent/dream_phase3_memory.md",
                strip=True,
                mode="create",
                existing_summary="",
                changelog="",
                category_content=category_content,
            )

        try:
            response = await self._runner.run(AgentRunSpec(
                initial_messages=[{"role": "user", "content": prompt}],
                tools=ToolRegistry(),
                model=self.model,
                max_iterations=1,
                max_tool_result_chars=self.max_tool_result_chars,
            ))

            if response and response.final_content:
                summary = response.final_content.strip()
                if not summary.startswith("#"):
                    summary = "# 用户记忆摘要\n\n" + summary
                if len(summary) > 50:
                    memory_md_path.write_text(summary, encoding="utf-8")
                    logger.info(
                        "Dream Phase 3: memory.md {} ({} chars)",
                        "updated" if existing else "created", len(summary),
                    )
                else:
                    logger.warning("Dream Phase 3: output too short ({} chars)", len(summary))
            else:
                logger.warning("Dream Phase 3: empty response")

        except Exception:
            logger.exception("Dream Phase 3 failed")

    # -- main entry ----------------------------------------------------------

    def _build_category_file_context(self) -> str:
        """Build a context block showing current contents of all category files."""
        current_date = datetime.now().strftime("%Y-%m-%d")
        parts = [f"## Current Date\n{current_date}"]

        if self._category_manager:
            # New CategoryManager directory structure
            for meta in self._category_manager._index.list_active():
                cat_id = meta.get("category_id", "")
                name = meta.get("name", "")
                cat_type = meta.get("type", "")
                content = self._category_manager.read_category_md(cat_id) or "(empty)"
                rel_path = meta.get("_path", name)
                parts.append(
                    f"## Current [{cat_type}] {name} — {rel_path} ({len(content)} chars)\n{content}"
                )
        else:
            # Fallback: old flat file structure
            for cat in self.store.CATEGORY_FILES:
                content = self.store.read_category(cat) or "(empty)"
                path = self.store.category_path(cat)
                parts.append(
                    f"## Current {path.relative_to(self.store.workspace)} ({len(content)} chars)\n{content}"
                )

        current_soul = self.store.read_soul() or "(empty)"
        current_user = self.store.read_user() or "(empty)"
        parts.append(f"## Current SOUL.md ({len(current_soul)} chars)\n{current_soul}")
        parts.append(f"## Current USER.md ({len(current_user)} chars)\n{current_user}")
        return "\n\n".join(parts)

    def _build_category_index(self) -> str:
        """Build a lightweight category index (summaries only, ~100 chars/file)."""
        parts = [f"## Current Date\n{datetime.now().strftime('%Y-%m-%d')}"]
        if self._category_manager:
            for meta in self._category_manager._index.list_active():
                cat_id = meta.get("category_id", "")
                name = meta.get("name", "")
                cat_type = meta.get("type", "custom")
                summary = self._category_manager.get_category_summary(cat_id)
                item_count = self._category_manager.count_items(cat_id)
                rel_path = Path(meta.get("_path", name)).name
                parts.append(f"- [{cat_type}] {name} ({rel_path}) — {summary} [{item_count} items]")
        current_soul = self.store.read_soul() or "(empty)"
        current_user = self.store.read_user() or "(empty)"
        parts.append(f"\n## SOUL.md\n{current_soul}")
        parts.append(f"\n## USER.md\n{current_user}")
        return "\n".join(parts)

    def _build_file_paths_section(self) -> str:
        """List all category file paths for AgentRunner's read_file tool."""
        if not self._category_manager:
            return ""
        lines = ["## Category File Paths (use read_file to load)"]
        for meta in self._category_manager._index.list_active():
            name = meta.get("name", "")
            path = meta.get("_path", "")
            lines.append(f"- {name}: {path}")
        return "\n".join(lines)

    async def _build_relevant_memories(self, batch: list[dict], top_k: int = 10) -> str:
        """Use vector retrieval to find memories relevant to the current batch."""
        if not self._memory_store_v2 or not self._embedding:
            return ""
        try:
            query_text = "\n".join(e.get("content", "") for e in batch[-5:])
            if not query_text.strip():
                return ""
            query_vec = self._embedding.embed(query_text)
            results = self._memory_store_v2.search_with_ranking(
                query_vec=query_vec, top_k=top_k, threshold=0.5,
            )
            if not results:
                return ""
            lines = ["## Relevant Memories (vector Top-K)"]
            for r in results:
                cat = r.get("category_id", "?")
                summary = r.get("summary", "")[:100]
                score = r.get("_final_score", 0)
                lines.append(f"- [{cat}] {summary} (score: {score:.2f})")
            return "\n".join(lines)
        except Exception:
            logger.debug("Dream: vector retrieval failed, skipping relevant memories")
            return ""

    # -- ResourceStore cursor --------------------------------------------------

    def _get_resource_cursor(self) -> int:
        """Get the line-count cursor for ResourceStore conversations."""
        cursor_file = self.store.memory_dir / ".dream_resource_cursor"
        if cursor_file.exists():
            try:
                return int(cursor_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                return 0
        return 0

    def _set_resource_cursor(self, cursor: int) -> None:
        """Save the line-count cursor for ResourceStore conversations."""
        cursor_file = self.store.memory_dir / ".dream_resource_cursor"
        cursor_file.write_text(str(cursor), encoding="utf-8")

    def _read_resource_entries(self) -> tuple[list[dict], int]:
        """Read unprocessed conversations from ResourceStore.

        Returns (entries, new_cursor). Each entry has 'timestamp' and 'content'.
        Conversations already processed by BatchExtractor (non-empty related_item_ids)
        are skipped to avoid duplicate extraction.
        """
        all_recs = self._resource_store._read_jsonl(self._resource_store._conv_path)
        cursor = self._get_resource_cursor()
        new_entries = all_recs[cursor:]
        if not new_entries:
            return [], cursor

        # Filter out conversations already processed by BatchExtractor.
        # BatchExtractor writes related_item_ids back after extraction,
        # so non-empty related_item_ids = already processed.
        unprocessed_recs = []
        for rec in new_entries:
            if rec.get("related_item_ids"):
                continue
            unprocessed_recs.append(rec)

        skipped = len(new_entries) - len(unprocessed_recs)
        if skipped:
            logger.info(
                "Dream: skipped {} conversations already processed by BatchExtractor",
                skipped,
            )

        if not unprocessed_recs:
            # All new entries were processed — advance cursor to avoid re-checking.
            return [], cursor + len(new_entries)

        # Normalize to the format expected by Dream: {timestamp, content}
        entries = []
        for rec in unprocessed_recs:
            ts = rec.get("metadata", {}).get("timestamp", "")
            if not ts:
                ts = rec.get("resource_id", "")
            entries.append({
                "timestamp": ts,
                "content": rec.get("content", ""),
            })
        return entries, cursor + len(new_entries), skipped

    # -- custom/ cleanup ---------------------------------------------------

    def _merge_to_other(self, md_file: Path, meta: dict) -> None:
        """Merge a custom category file into misc.md, then delete the original."""
        if not self._category_manager:
            return
        try:
            body = md_file.read_text(encoding="utf-8")
            # Strip frontmatter
            if body.startswith("---"):
                parts = body.split("---", 2)
                body = parts[2] if len(parts) > 2 else ""
            body = body.strip()
            if not body:
                # Nothing to merge, just delete
                md_file.unlink(missing_ok=True)
                self._category_manager._index.remove(meta.get("category_id", ""))
                return

            # Ensure misc.md exists
            self._category_manager.get_or_create_category(
                name="misc", type="custom",
            )
            misc_path = self._category_manager._memory_dir / "custom" / "misc.md"
            existing = misc_path.read_text(encoding="utf-8") if misc_path.exists() else ""

            # Append body with source tag
            source_name = meta.get("name", md_file.stem)
            merged = existing.rstrip() + f"\n\n<!-- merged from {source_name} -->\n{body}"
            misc_path.write_text(merged, encoding="utf-8")

            # Delete original and remove from index
            md_file.unlink(missing_ok=True)
            self._category_manager._index.remove(meta.get("category_id", ""))
            logger.info("Dream: merged custom/{} → custom/misc.md", source_name)
        except Exception:
            logger.exception("Failed to merge {} to misc.md", md_file.name)

    async def _cleanup_stale_custom_categories(
        self, *, max_age_days: int = 30, min_items: int = 2,
    ) -> int:
        """Merge stale custom categories into misc.md and delete originals.

        Stale = older than max_age_days OR fewer than min_items items.
        """
        if not self._category_manager:
            return 0
        custom_dir = self._category_manager._memory_dir / "custom"
        if not custom_dir.exists():
            return 0

        archived = 0
        for md_file in custom_dir.glob("*.md"):
            if md_file.name == "misc.md":
                continue

            meta = self._category_manager._index.get_by_path(str(md_file))
            if not meta:
                continue

            # Condition 1: too old
            last_updated = meta.get("updated_at", "")
            if last_updated:
                try:
                    dt = datetime.fromisoformat(last_updated)
                    if (datetime.now(timezone.utc) - dt).days > max_age_days:
                        self._merge_to_other(md_file, meta)
                        archived += 1
                        continue
                except ValueError:
                    pass

            # Condition 2: too few items
            content = md_file.read_text(encoding="utf-8")
            body = content.split("---", 2)[-1] if "---" in content else content
            item_count = sum(1 for line in body.splitlines() if line.strip().startswith("- "))
            if item_count < min_items:
                self._merge_to_other(md_file, meta)
                archived += 1

        if archived:
            logger.info("Dream: archived {} stale custom categories", archived)
        return archived

    async def run(self) -> bool:
        """Process unprocessed history entries. Returns True if work was done."""
        from fincat.agent.skills import BUILTIN_SKILLS_DIR

        # Read from ResourceStore (new) or MemoryStore history (fallback)
        try:
            if self._resource_store:
                entries, new_cursor, skipped_count = self._read_resource_entries()
            else:
                last_cursor = self.store.get_last_dream_cursor()
                entries = self.store.read_unprocessed_history(since_cursor=last_cursor)
                new_cursor = entries[-1]["cursor"] if entries else last_cursor
        except Exception:
            logger.exception("Dream: failed to read resource entries")
            return False
        if not entries:
            # Still advance cursor if _read_resource_entries computed a new_cursor
            # (handles the case where all entries were filtered by related_item_ids)
            if self._resource_store and new_cursor > self._get_resource_cursor():
                self._set_resource_cursor(new_cursor)
                logger.info(
                    "Dream: all entries already processed by BatchExtractor, "
                    "cursor advanced to {}",
                    new_cursor,
                )
            # Even with no new entries, generate memory.md if it doesn't exist
            if self._category_manager:
                memory_md_path = self._category_manager._memory_dir / "memory.md"
                if not memory_md_path.exists():
                    logger.info("Dream: no new entries but memory.md missing, generating")
                    try:
                        await self._regenerate_memory_md_with_llm()
                    except Exception:
                        logger.exception("Dream: memory.md generation failed")
            return False

        batch = entries[: self.max_batch_size]
        if self._resource_store:
            logger.info(
                "Dream: processing {} resource entries (cursor {}→{}), batch={}",
                len(entries), new_cursor - len(batch), new_cursor, len(batch),
            )
        else:
            logger.info(
                "Dream: processing {} entries (cursor {}→{}), batch={}",
                len(entries), new_cursor, batch[-1]["cursor"], len(batch),
            )

        # Build history text for LLM
        history_text = "\n".join(
            f"[{e.get('timestamp', '')}] {e['content']}" for e in batch
        )

        # Lightweight context for Phase 1/2: category index + vector retrieval
        category_index = self._build_category_index()
        relevant_memories = await self._build_relevant_memories(batch)

        # Include prefilter queue if available
        prefilter_section = ""
        if self._prefilter:
            queue_entries = self._prefilter.read_queue()
            if queue_entries:
                lines = [
                    f"- [{e.get('category', '?')}] {e.get('content', '')}"
                    for e in queue_entries
                ]
                prefilter_section = (
                    "\n\n## Pre-filter Queue (medium-confidence items from real-time rules)\n"
                    + "\n".join(lines)
                    + "\n\nFor each item above: CONFIRM (keep as-is), "
                    "RECLASSIFY (wrong category), or DISCARD (not worth remembering)."
                )

        # Phase 1: Analyze (no skills list — dedup is Phase 2's job)
        phase1_context = f"{category_index}"
        if relevant_memories:
            phase1_context += f"\n\n{relevant_memories}"
        phase1_prompt = (
            f"## Conversation History\n{history_text}\n\n{phase1_context}{prefilter_section}"
        )

        try:
            phase1_response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template("agent/dream_phase1.md", strip=True),
                    },
                    {"role": "user", "content": phase1_prompt},
                ],
                tools=None,
                tool_choice=None,
            )
            analysis = phase1_response.content or ""
            logger.debug("Dream Phase 1 analysis ({} chars): {}", len(analysis), analysis[:500])

            # Extract atomic MemoryItems from Phase 1 [FILE] lines
            before_count = len(self._item_store) if self._item_store else 0
            await self._extract_memory_items_from_analysis(analysis, new_cursor)
            after_count = len(self._item_store) if self._item_store else 0
            if after_count == before_count and self._item_store:
                # Regex didn't match — Phase 1 output was prose, use LLM fallback
                await self._extract_memory_items_llm_fallback(analysis, new_cursor)

            # Clear prefilter queue after successful Phase 1
            if self._prefilter and prefilter_section:
                self._prefilter.clear_queue()
                logger.info("Dream: cleared prefilter queue after Phase 1")
        except Exception:
            logger.exception("Dream Phase 1 failed")
            return False

        # Phase 2: Delegate to AgentRunner with read_file / edit_file
        existing_skills = self._list_existing_skills()
        skills_section = ""
        if existing_skills:
            skills_section = (
                "\n\n## Existing Skills\n"
                + "\n".join(f"- {s}" for s in existing_skills)
            )

        # Inject recent SkillEvolver changes to avoid duplicate creation
        recent_evolutions = self._read_recent_skill_evolutions(hours=24)
        if recent_evolutions:
            skills_section += "\n\n## Recently Created/Modified (last 24h)\n"
            skills_section += "\n".join(
                f"- {e.get('skill_name', e.get('name', '?'))} ({e.get('event_type', e.get('action', '?'))}): {e.get('metadata', {}).get('reason', e.get('reason', ''))}"
                for e in recent_evolutions
            )
            skills_section += "\n\nDo NOT create skills that overlap with the above."

        file_paths_section = self._build_file_paths_section()
        phase2_prompt = f"## Analysis Result\n{analysis}\n\n{category_index}\n\n{file_paths_section}{skills_section}"

        tools = self._tools
        skill_creator_path = BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": render_template(
                    "agent/dream_phase2.md",
                    strip=True,
                    skill_creator_path=str(skill_creator_path),
                ),
            },
            {"role": "user", "content": phase2_prompt},
        ]

        try:
            result = await self._runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=self.max_iterations,
                max_tool_result_chars=self.max_tool_result_chars,
                fail_on_tool_error=False,
            ))
            logger.debug(
                "Dream Phase 2 complete: stop_reason={}, tool_events={}",
                result.stop_reason, len(result.tool_events),
            )
            for ev in (result.tool_events or []):
                logger.info("Dream tool_event: name={}, status={}, detail={}", ev.get("name"), ev.get("status"), ev.get("detail", "")[:200])
        except Exception:
            logger.exception("Dream Phase 2 failed")
            result = None

        # Build changelog from tool events
        changelog: list[str] = []
        if result and result.tool_events:
            for event in result.tool_events:
                if event.get("status") == "ok":
                    changelog.append(f"{event.get('name', '?')}: {event.get('detail', '')}")

        # ---- Phase 3: Regenerate memory.md with LLM summarization ----
        try:
            await self._regenerate_memory_md_with_llm(changelog=changelog)
        except Exception:
            logger.exception("Dream Phase 3 (memory.md summary) failed, keeping previous memory.md")

        # Validate any SKILL.md files created/modified by Phase 2
        if changelog:
            self._validate_dream_skills(result)

        # Advance cursor — always, to avoid re-processing Phase 1
        # Only advance past entries we actually processed (skipped + batch),
        # NOT past unprocessed entries that remain for the next Dream run.
        if self._resource_store:
            actual_cursor = self._get_resource_cursor() + skipped_count + len(batch)
            self._set_resource_cursor(actual_cursor)
            new_cursor = actual_cursor  # for logging below
        else:
            self.store.set_last_dream_cursor(new_cursor)
            self.store.compact_history()

        if result and result.stop_reason == "completed":
            logger.info(
                "Dream done: {} change(s), cursor advanced to {}",
                len(changelog), new_cursor,
            )
        else:
            reason = result.stop_reason if result else "exception"
            logger.warning(
                "Dream incomplete ({}): cursor advanced to {}",
                reason, new_cursor,
            )

        # Run PatternMiner after Dream to extract patterns from new data
        if self._pattern_miner and self._dynamic_rule_store:
            try:
                rules = await self._pattern_miner.run_daily()
                for r in rules:
                    self._dynamic_rule_store.add_rule(r)
                for r in rules:
                    if r.get("type") == "predict" and self._prediction_engine:
                        self._prediction_engine.add_dynamic_rule(r)
                self._dynamic_rule_store.cleanup_expired()
                logger.info("Dream: PatternMiner produced {} rules", len(rules))
            except Exception:
                logger.exception("Dream: PatternMiner failed (non-fatal)")

        # Cleanup stale custom categories (non-fatal)
        try:
            await self._cleanup_stale_custom_categories()
        except Exception:
            logger.exception("Dream: custom category cleanup failed (non-fatal)")

        # Git auto-commit (only when there are actual changes)
        if changelog and self.store.git.is_initialized():
            ts = batch[-1]["timestamp"]
            sha = self.store.git.auto_commit(f"dream: {ts}, {len(changelog)} change(s)")
            if sha:
                logger.info("Dream commit: {}", sha)

        return True

    # -- 过时记忆处理 --------------------------------------------------------

    async def archive_stale_memories(self, *, archive_days: int = 180, decay_threshold: float = 0.1) -> int:
        """归档长期未访问的记忆。

        规则：
        - 180 天以上未访问 → 归档到冷存储
        - 衰减分数低于阈值 → 归档到冷存储

        Returns:
            归档的记忆数量
        """
        if not self._item_store:
            return 0

        now = datetime.now(timezone.utc)
        archived_count = 0

        for item in self._item_store.all():
            # 跳过已归档的
            if item.is_archived:
                continue

            # 计算未访问天数
            ref = item.last_accessed or item.timestamp
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            days_since_access = (now - ref).total_seconds() / 86400

            # 计算衰减分数
            item.compute_decay(now=now)

            # 归档条件
            should_archive = (
                days_since_access >= archive_days
                or item.decay_score < decay_threshold
            )

            if should_archive:
                item.is_archived = True
                if self._item_store.archive(item.item_id):
                    archived_count += 1
                    logger.info(
                        "Archived stale memory: id={} days={:.0f} decay={:.3f}",
                        item.item_id, days_since_access, item.decay_score,
                    )

        if archived_count:
            logger.info("Archived {} stale memories", archived_count)

        return archived_count

    def search_memories_with_conflict_hint(self, query: str, limit: int = 10) -> list[dict]:
        """检索记忆，附带冲突提示。

        如果检索到的记忆存在冲突标记，会在结果中添加冲突提示信息。
        """
        if not self._memory_store_v2:
            return []

        results = self._memory_store_v2.search_similar_batch(
            query, threshold=0.7, limit=limit,
        )

        # 检查冲突标记
        for r in results:
            extra = r.get("extra", {})
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except (json.JSONDecodeError, TypeError):
                    extra = {}

            conflict_with = extra.get("conflict_with")
            if conflict_with:
                # 获取冲突记忆的信息
                conflict_item = self._memory_store_v2.get_item(conflict_with)
                if conflict_item:
                    r["_conflict_hint"] = {
                        "conflict_id": conflict_with,
                        "conflict_summary": conflict_item.get("summary", "")[:100],
                        "message": f"此记忆与另一条记忆存在冲突，请确认正确版本",
                    }

        return results
