"""Memory system: pure file I/O store, lightweight Consolidator, and Dream processor."""

from __future__ import annotations

import asyncio
import json
import re
import time
import weakref
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
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
# Date absolutification — pure regex, zero LLM
# ---------------------------------------------------------------------------

_WEEKDAY_MAP = {"周一": 0, "周二": 1, "周三": 2, "周四": 3,
                "周五": 4, "周六": 5, "周日": 6, "周天": 6}

_RELATIVE_DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 长模式优先，避免"前天"先匹配"大前天"的一部分
    (re.compile(r"上上个(周[一二三四五六日天])"), "2w_before_weekday"),
    (re.compile(r"上个(周[一二三四五六日天])"), "1w_before_weekday"),
    (re.compile(r"这个(周[一二三四五六日天])"), "this_week_weekday"),
    (re.compile(r"大前天"), "3_days_ago"),
    (re.compile(r"前天"), "2_days_ago"),
    (re.compile(r"昨天"), "1_day_ago"),
    (re.compile(r"今天"), "today"),
    (re.compile(r"明天"), "tomorrow"),
    (re.compile(r"后天"), "2_days_later"),
    (re.compile(r"大后天"), "3_days_later"),
    (re.compile(r"上个月"), "1_month_ago"),
    (re.compile(r"这个月"), "this_month"),
    (re.compile(r"去年"), "1_year_ago"),
    (re.compile(r"今年"), "this_year"),
]


def absolutify_dates(text: str, now: datetime | None = None) -> str:
    """将中文相对时间词替换为绝对日期。纯正则，无 LLM 开销。"""
    if now is None:
        now = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
    result = text

    def _replace_weekday(match: re.Match, weeks_back: int) -> str:
        wd_name = match.group(1)
        target_wd = _WEEKDAY_MAP.get(wd_name, 0)
        current_wd = now.weekday()
        days_back = (current_wd - target_wd) % 7 + weeks_back * 7
        if days_back == 0 and weeks_back > 0:
            days_back = 7
        target_date = now - timedelta(days=days_back)
        return target_date.strftime("%Y-%m-%d") + f"({wd_name})"

    for pattern, kind in _RELATIVE_DATE_PATTERNS:
        if kind == "2w_before_weekday":
            result = pattern.sub(lambda m: _replace_weekday(m, 2), result)
        elif kind == "1w_before_weekday":
            result = pattern.sub(lambda m: _replace_weekday(m, 1), result)
        elif kind == "this_week_weekday":
            result = pattern.sub(lambda m: _replace_weekday(m, 0), result)
        elif kind == "3_days_ago":
            result = pattern.sub((now - timedelta(days=3)).strftime("%Y-%m-%d"), result)
        elif kind == "2_days_ago":
            result = pattern.sub((now - timedelta(days=2)).strftime("%Y-%m-%d"), result)
        elif kind == "1_day_ago":
            result = pattern.sub((now - timedelta(days=1)).strftime("%Y-%m-%d"), result)
        elif kind == "today":
            result = pattern.sub(now.strftime("%Y-%m-%d"), result)
        elif kind == "tomorrow":
            result = pattern.sub((now + timedelta(days=1)).strftime("%Y-%m-%d"), result)
        elif kind == "2_days_later":
            result = pattern.sub((now + timedelta(days=2)).strftime("%Y-%m-%d"), result)
        elif kind == "3_days_later":
            result = pattern.sub((now + timedelta(days=3)).strftime("%Y-%m-%d"), result)
        elif kind == "1_month_ago":
            m_val = now.month - 1 or 12
            y_val = now.year if now.month > 1 else now.year - 1
            result = pattern.sub(f"{y_val}年{m_val}月", result)
        elif kind == "this_month":
            result = pattern.sub(f"{now.year}年{now.month}月", result)
        elif kind == "1_year_ago":
            result = pattern.sub(f"{now.year - 1}年", result)
        elif kind == "this_year":
            result = pattern.sub(f"{now.year}年", result)

    return result


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

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_batch_size: int = 20,
        max_iterations: int = 10,
        max_tool_result_chars: int = 16_000,
        item_store: Any = None,  # MemoryItemStore, optional (deprecated)
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
        self._item_store = item_store  # deprecated, kept for backward compat
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

        # Extraction pool
        self._extraction_pending: list[dict] = []  # {resource_id, content}
        self._pending_resource_ids: set[str] = set()  # O(1) dedup
        self._last_extraction_time: float = 0.0
        self._extraction_pool_size: int = 50
        self._extraction_interval_sec: int = 7200  # 2 hours
        self._last_changelog: list[str] = []  # run_extraction 缓存
        self._last_items_by_type: dict[str, list[dict]] = {}  # run_extraction 缓存

        # PatternStore (lazy init)
        self._pattern_store: Any = None

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

    # -- helpers -------------------------------------------------------------

    def _get_items_by_type(self, memory_type: str) -> list[dict]:
        """Read existing items from SQLite for a given memory_type."""
        if not self._memory_store_v2:
            return []
        try:
            rows = self._memory_store_v2.query(memory_type=memory_type, limit=200)
            return [
                {
                    "item_id": r.get("item_id", ""),
                    "summary": r.get("summary", ""),
                    "memory_type": r.get("memory_type", memory_type),
                }
                for r in rows
            ]
        except Exception:
            logger.debug("Failed to query items for type {}", memory_type)
            return []

    # -- main entry ----------------------------------------------------------

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

    def _read_resource_entries(self) -> tuple[list[dict], int, int]:
        """Read unprocessed conversations from ResourceStore.

        Returns (entries, new_cursor, skipped_count).
        Conversations with non-empty related_item_ids are skipped (already extracted).
        Cursor only advances when ALL remaining records are processed.
        """
        all_recs = self._resource_store._read_jsonl(self._resource_store._conv_path)
        cursor = self._get_resource_cursor()
        remaining = all_recs[cursor:]
        if not remaining:
            return [], cursor, 0

        entries = []
        skipped = 0
        for rec in remaining:
            if rec.get("related_item_ids"):
                skipped += 1
                continue
            ts = rec.get("metadata", {}).get("timestamp", "")
            if not ts:
                ts = rec.get("resource_id", "")
            entries.append({
                "resource_id": rec.get("resource_id", ""),
                "timestamp": ts,
                "content": rec.get("content", ""),
            })

        if skipped:
            logger.info(
                "Dream: skipped {} already-processed conversations",
                skipped,
            )

        # Only advance cursor when ALL remaining records are processed
        if not entries:
            new_cursor = cursor + len(remaining)
        else:
            # Don't advance — unprocessed records exist, re-read them next time
            new_cursor = cursor
        return entries, new_cursor, skipped

    # -- extraction pool -----------------------------------------------------

    def _init_pattern_store(self) -> None:
        """Lazy-init PatternStore."""
        if self._pattern_store or not self._memory_store_v2 or not self._embedding:
            return
        from fincat.agent.pattern_store import PatternStore
        db_path = self.store.workspace / "memory" / "patterns.db"
        self._pattern_store = PatternStore(db_path, self._embedding)

    def add_to_extraction(self, resource_id: str, content: str) -> None:
        """将对话加入待提取池（自动去重 resource_id）。"""
        if resource_id and resource_id in self._pending_resource_ids:
            return
        self._extraction_pending.append({
            "resource_id": resource_id,
            "content": content,
        })
        if resource_id:
            self._pending_resource_ids.add(resource_id)
        logger.debug(
            "Dream extraction pool: added {} (total={})",
            resource_id, len(self._extraction_pending),
        )

    def populate_extraction_from_resources(self) -> bool:
        """从 ResourceStore 读取未处理对话并加入提取池。

        公开方法，供 cron/dream 命令调用，不暴露内部实现。
        Returns: True 如果有新 entries 被加入池。
        """
        if not self._resource_store:
            return False
        entries, new_cursor, _ = self._read_resource_entries()
        for e in entries:
            self.add_to_extraction(e["resource_id"], e["content"])
        if new_cursor > self._get_resource_cursor():
            self._set_resource_cursor(new_cursor)
        return bool(entries)

    def should_extract(self) -> bool:
        """判断是否触发 Phase 1 提取。"""
        if len(self._extraction_pending) >= self._extraction_pool_size:
            return True
        if self._extraction_pending:
            if time.time() - self._last_extraction_time > self._extraction_interval_sec:
                return True
        return False

    async def run_extraction(self) -> tuple[list[str], dict[str, list[dict]], int, int]:
        """Phase 1 only: 从待提取池中循环提取 items + patterns，直到池空。

        Returns: (changelog, new_items_by_type, total_batches, total_items)
          - changelog: 变更日志
          - new_items_by_type: {memory_type: [{item_id, summary, memory_type}]}
            供 Phase 2 直接使用，无需再读 items.jsonl
          - total_batches: 处理的 batch 数
          - total_items: 提取的 item 总数
        """
        if not self._extraction_pending:
            logger.info("Dream extraction: nothing to process")
            return [], {}, 0, 0

        self._init_pattern_store()

        all_changelog: list[str] = []
        all_items_by_type: dict[str, list[dict]] = {}
        batch_num = 0
        total_resources = len(self._extraction_pending)

        while self._extraction_pending:
            batch_num += 1
            # 取出待处理的 resources
            batch = self._extraction_pending[:self.max_batch_size]
            self._extraction_pending = self._extraction_pending[self.max_batch_size:]
            for r in batch:
                self._pending_resource_ids.discard(r["resource_id"])

            logger.info(
                "Dream extraction: batch {}/{} processing {} resources ({} remaining)",
                batch_num, (total_resources + self.max_batch_size - 1) // self.max_batch_size,
                len(batch), len(self._extraction_pending),
            )

            success, changelog, new_items_by_type = await self._extract_batch(batch)

            if not success:
                # LLM 失败，batch 已放回池中，跳出循环
                logger.warning("Dream extraction: batch {} failed, stopping", batch_num)
                break

            all_changelog.extend(changelog)
            batch_item_count = sum(len(v) for v in new_items_by_type.values())
            for mt, items in new_items_by_type.items():
                all_items_by_type.setdefault(mt, []).extend(items)

            logger.info(
                "Dream extraction: batch {} done — {} items extracted, {} changelog entries",
                batch_num, batch_item_count, len(changelog),
            )

        self._last_extraction_time = time.time()
        self._last_changelog = all_changelog
        self._last_items_by_type = all_items_by_type

        total_items = sum(len(v) for v in all_items_by_type.values())
        logger.info(
            "Dream extraction done: {}/{} batches, {} resources, {} items",
            batch_num, (total_resources + self.max_batch_size - 1) // self.max_batch_size,
            total_resources, total_items,
        )

        return all_changelog, all_items_by_type, batch_num, total_items

    async def _extract_batch(
        self, batch: list[dict],
    ) -> tuple[bool, list[str], dict[str, list[dict]]]:
        """处理单个 batch：LLM 提取 → 后处理。

        Returns: (success, changelog, new_items_by_type)
          - success: True if LLM responded (even if 0 items), False if LLM failed
        """
        # 构建 LLM prompt
        for r in batch:
            preview = r["content"][:80].replace("\n", " ")
            logger.debug("  {} — {}", r["resource_id"], preview)

        resources_json = json.dumps(
            [{"resource_id": r["resource_id"], "content": r["content"][:2000]} for r in batch],
            ensure_ascii=False, indent=2,
        )
        prompt = render_template("agent/dream_extract.md", strip=True)
        prompt = prompt.replace("{{resources}}", resources_json)

        # 补充已有记忆上下文
        category_index = self._build_category_index()
        relevant = await self._build_relevant_memories(
            [{"content": r["content"]} for r in batch]
        )
        user_prompt = f"{category_index}\n\n{relevant}\n\n## 待提取对话\n{resources_json}"

        try:
            response = await asyncio.wait_for(
                self.provider.chat_with_retry(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=None, tool_choice=None,
                ),
                timeout=180,  # 3 minutes per batch
            )
            raw = response.content or ""
        except asyncio.TimeoutError:
            logger.error("Dream extraction: LLM call timed out (180s)")
            self._extraction_pending = batch + self._extraction_pending
            for r in batch:
                self._pending_resource_ids.add(r["resource_id"])
            return False, [], {}
        except Exception:
            logger.exception("Dream extraction: LLM call failed")
            self._extraction_pending = batch + self._extraction_pending
            for r in batch:
                self._pending_resource_ids.add(r["resource_id"])
            return False, [], {}

        # 解析 JSON
        extracted = self._parse_extraction_json(raw)
        if not extracted:
            logger.warning("Dream extraction: failed to parse LLM output")
            return False, [], {}

        items = extracted.get("items", [])
        patterns = extracted.get("patterns", {})

        logger.info(
            "Dream extraction: got {} items, {} temporal, {} entity, {} semantic",
            len(items),
            len(patterns.get("temporal", [])),
            len(patterns.get("entity_relations", [])),
            len(patterns.get("semantic_patterns", [])),
        )

        # 后处理 items
        changelog, new_items_by_type = await self._post_process_items(items, batch)

        # 保存 patterns
        if self._pattern_store and patterns:
            self._save_patterns(patterns, batch)

        return True, changelog, new_items_by_type

    def _parse_extraction_json(self, raw: str) -> dict | None:
        """解析 Phase 1 LLM 输出的 JSON。"""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        # 尝试找到 JSON 块
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None

    async def _post_process_items(
        self, items: list[dict], resources: list[dict],
    ) -> tuple[list[str], dict[str, list[dict]]]:
        """Phase 1 后处理: 向量去重 → 冲突检测 → 写入 SQLite → 回写 resource → 追加 items.jsonl。

        Returns: (changelog, new_items_by_type)
          - changelog: 变更日志字符串列表
          - new_items_by_type: {memory_type: [{item_id, summary, memory_type}, ...]}
            仅供 Phase 2 使用，只有 status=new/conflict/overwrite 的 items
        """
        changelog: list[str] = []
        new_items_by_type: dict[str, list[dict]] = {}
        jsonl_records: list[dict] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for item_data in items:
            summary = item_data.get("summary", "")
            if not summary or len(summary) < 4:
                continue

            resource_id = item_data.get("resource_id", "")
            memory_type = item_data.get("memory_type", "custom")
            source_type = item_data.get("source_type", "user")

            # source_type=agent 降低重要性
            importance = item_data.get("importance_score", 0.5)
            if source_type == "agent":
                importance = min(importance, 0.4)

            # 向量去重
            status, existing_id = await self._dedup_item(summary, item_data)

            if status in ("dedup", "superseded", "merge"):
                jsonl_records.append({
                    "item_id": existing_id or "",
                    "resource_id": resource_id,
                    "memory_type": memory_type,
                    "summary": summary,
                    "status": status,
                    "existing_item_id": existing_id,
                    "created_at": now_iso,
                })
                if status in ("dedup", "merge"):
                    changelog.append(f"[{status}] {summary[:50]}")
                continue

            # overwrite: 停用旧 item，然后写入新 item
            if status == "overwrite" and existing_id and self._memory_store_v2:
                try:
                    self._memory_store_v2.deactivate_item(existing_id)
                    logger.debug("Dream: deactivated old item {} (overwritten by new)", existing_id)
                except Exception:
                    logger.exception("Dream: failed to deactivate old item {}", existing_id)

            # new 或 conflict: 写入 SQLite
            item_id = None
            if self._memory_store_v2:
                try:
                    item_id = self._memory_store_v2.add_item(
                        resource_id=resource_id,
                        memory_type=memory_type,
                        summary=summary,
                        content=item_data.get("content", ""),
                        importance_score=importance,
                        entities=item_data.get("entities", []),
                        tags=item_data.get("tags", []),
                        source_type=source_type,
                    )
                    self._memory_store_v2.embed_and_index(item_id, summary)
                except Exception:
                    logger.exception("Dream extraction: SQLite write failed")

            # 回写 resource.related_item_ids
            if item_id and resource_id and self._resource_store:
                try:
                    existing = self._resource_store.get_by_resource_id(resource_id)
                    if existing:
                        ids = existing.get("related_item_ids", [])
                        ids.append(item_id)
                        self._resource_store.update_related_items(resource_id, ids)
                except Exception:
                    logger.debug("Resource backfill failed for {}", resource_id)

            jsonl_records.append({
                "item_id": item_id or "",
                "resource_id": resource_id,
                "memory_type": memory_type,
                "summary": summary,
                "importance_score": importance,
                "entities": item_data.get("entities", []),
                "tags": item_data.get("tags", []),
                "source_type": source_type,
                "status": status,
                "frequency": 1,
                "confidence": 0.5,
                "conflict_with": None,
                "created_at": now_iso,
                "existing_item_id": existing_id,
            })

            # 收集新 items 供 Phase 2 使用
            if item_id:
                new_items_by_type.setdefault(memory_type, []).append({
                    "item_id": item_id,
                    "summary": summary,
                    "memory_type": memory_type,
                })

            changelog.append(f"[{status}] {memory_type}: {summary[:50]}")

        # 批量写入 items.jsonl（单次文件打开）
        self._flush_items_jsonl(jsonl_records)

        return changelog, new_items_by_type

    async def _dedup_item(
        self, summary: str, item_data: dict,
    ) -> tuple[str, str | None]:
        """向量去重 + 冲突检测。返回 (status, existing_item_id)。"""
        if not self._memory_store_v2 or not self._embedding:
            return "new", None

        try:
            # touch=False: search_similar 不自动 touch，由本方法按需调用
            similar = self._memory_store_v2.search_similar(summary, threshold=0.85, touch=False)
        except Exception:
            return "new", None

        if not similar:
            return "new", None

        score = similar.get("_score", 0)
        existing_id = similar.get("item_id", "")

        # ≥0.95: 精确去重
        if score >= 0.95:
            self._memory_store_v2.touch_item(existing_id)
            return "dedup", existing_id

        # 0.85-0.95: 冲突检测
        if self._conflict_detector:
            try:
                result = await self._conflict_detector.detect_and_resolve(
                    new_content=summary,
                    new_timestamp=datetime.now(timezone.utc),
                    new_source_type=item_data.get("source_type", "user"),
                    new_confidence=0.5,
                    new_frequency=1,
                    existing_content=similar.get("summary", ""),
                    existing_id=existing_id,
                    existing_timestamp=datetime.fromisoformat(
                        similar.get("created_at", datetime.now(timezone.utc).isoformat())
                    ),
                    existing_source_type=similar.get("extra", {}).get("source_type", "user"),
                    existing_confidence=0.5,
                    existing_frequency=similar.get("access_count", 1),
                )
                if not result.is_conflict:
                    self._memory_store_v2.touch_item(existing_id)
                    return "merge", existing_id
                if result.action == "overwrite":
                    if result.winner_id == "new":
                        return "overwrite", existing_id
                    return "superseded", existing_id
                if result.action == "keep_both":
                    return "conflict", existing_id
            except Exception:
                logger.debug("ConflictDetector failed, treating as merge")
                self._memory_store_v2.touch_item(existing_id)
                return "merge", existing_id

        # 没有 ConflictDetector，当作 merge
        self._memory_store_v2.touch_item(existing_id)
        return "merge", existing_id

    def _flush_items_jsonl(self, records: list[dict]) -> None:
        """批量追加记录到 items.jsonl（单次文件打开）。"""
        if not records:
            return
        items_path = self.store.workspace / "memory" / "items.jsonl"
        try:
            with open(items_path, "a", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to write items.jsonl")

    def _save_patterns(self, patterns: dict, resources: list[dict]) -> None:
        """保存 patterns 到 PatternStore（向量去重 + occurrence_count 累计）。"""
        if not self._pattern_store:
            return

        resource_ids = [r["resource_id"] for r in resources]

        for temporal in patterns.get("temporal", []):
            desc = temporal.get("description", "")
            if not desc:
                continue
            self._pattern_store.add_or_merge(
                pattern_type="temporal",
                description=desc,
                evidence_ids=temporal.get("evidence_resource_ids", resource_ids),
                confidence=temporal.get("confidence", 0.5),
                periodicity=temporal.get("periodicity_hint"),
                occurrence_count=temporal.get("occurrence_count", 1),
            )

        for entity_rel in patterns.get("entity_relations", []):
            desc = f"{entity_rel.get('subject', '')} → {entity_rel.get('relation', '')} → {entity_rel.get('object', '')}"
            if len(desc) < 5:
                continue
            self._pattern_store.add_or_merge(
                pattern_type="entity_relation",
                description=desc,
                evidence_ids=entity_rel.get("evidence_resource_ids", resource_ids),
                confidence=entity_rel.get("confidence", 0.5),
                occurrence_count=entity_rel.get("occurrence_count", 1),
                extra={
                    "subject": entity_rel.get("subject"),
                    "relation": entity_rel.get("relation"),
                    "object": entity_rel.get("object"),
                },
            )

        for semantic in patterns.get("semantic_patterns", []):
            desc = semantic.get("description", "")
            if not desc:
                continue
            self._pattern_store.add_or_merge(
                pattern_type="semantic",
                description=desc,
                evidence_ids=semantic.get("evidence_resource_ids", resource_ids),
                confidence=0.5,
                occurrence_count=semantic.get("occurrence_count", 1),
                extra={
                    "sequence": semantic.get("sequence", []),
                    "intervention_point": semantic.get("intervention_point"),
                },
            )

    async def _update_category_structure(
        self, memory_type: str, new_items: list[dict],
    ) -> None:
        """Phase 2: 更新 category .md 的三层结构（正文 + summary + items）。"""
        if not self._category_manager or not new_items:
            return

        # 找到对应的 category .md
        cat_meta = None
        for meta in self._category_manager._index.list_active():
            if meta.get("type") == memory_type:
                cat_meta = meta
                break
        if not cat_meta:
            return

        cat_path = Path(cat_meta["_path"])
        if not cat_path.exists():
            return

        # 读取当前 .md 内容
        current_content = cat_path.read_text(encoding="utf-8")

        # item_id 去重: 跳过已有 items
        existing_ids = set(re.findall(r"item_id:\s*(\S+)", current_content))
        new_items_filtered = [
            it for it in new_items
            if it.get("item_id") and it["item_id"] not in existing_ids
        ]
        if not new_items_filtered:
            return

        # 格式化新 items 为 markdown 行
        today = datetime.now().strftime("%Y-%m-%d")
        emoji_map = {
            "preference": "✨", "knowledge": "📚", "profile": "👤",
            "compliance": "⚖️", "behavior": "📊", "custom": "📌",
        }
        emoji = emoji_map.get(memory_type, "📌")
        new_lines = []
        for it in new_items_filtered:
            line = f"- [{today}] {emoji}【{memory_type}】{it.get('summary', '')} | item_id: {it['item_id']}"
            new_lines.append(line)

        # 追加到 "## 记忆条目" section
        if "## 记忆条目" in current_content:
            # 在 section 末尾追加
            parts = current_content.split("## 记忆条目")
            if len(parts) >= 2:
                updated = parts[0] + "## 记忆条目" + parts[1].rstrip() + "\n" + "\n".join(new_lines) + "\n"
            else:
                updated = current_content + "\n" + "\n".join(new_lines) + "\n"
        else:
            updated = current_content.rstrip() + "\n\n## 记忆条目\n" + "\n".join(new_lines) + "\n"

        # LLM 更新正文结构 + YAML summary
        category_guide = {
            "preference": "用户偏好、风格、沟通习惯、推送偏好等",
            "profile": "用户画像、基本信息、资产状况、投资经验、风险承受能力等",
            "knowledge": "产品知识、市场机制、金融概念、投资工具使用方法等",
            "compliance": "合规规则、风险提示、适当性管理、监管要求等",
            "behavior": "行为洞察、活跃时段、决策模式、学习轨迹、习惯规律等",
            "custom": "不属于以上分类的其他内容",
        }
        guide = category_guide.get(memory_type, "通用分类")
        try:
            update_prompt = f"""以下是一个记忆 category 文件的当前内容和新增条目。
category 类型：{memory_type}（{guide}）

## 三层结构要求

### 第一层：YAML frontmatter
保留原有 frontmatter，更新 summary 字段为一句话概述。

### 第二层：正文结构化 sections
用 ## 分 section，每个 section 聚焦一个主题，用 - 分点列出要点。
参考示例：
```
## Risk Appetite
- 风险偏好低，偏好稳定收益产品

## Investment Interests
- 用户关注西安购房政策
- 用户关注新能源电池行业

## Communication Style
- 偏好纯中文沟通
- 偏好简洁方案
```
正文 section 应覆盖该 category 的主要维度，新信息融入已有 section 或新建 section。

### 第三层：记忆条目
## 记忆条目 下逐条列出原始提取记录（带 item_id）。

## 当前文件内容
{updated}

## 新增条目
{chr(10).join(new_lines)}

## 输出要求
输出完整的更新后文件内容（含 YAML frontmatter）。确保：
1. 正文有多个 ## section，每个 section 内用 - 分点
2. YAML summary 反映最新内容概述
3. 记忆条目保留所有 item_id
只输出文件内容，不要添加解释。"""

            response = await asyncio.wait_for(
                self.provider.chat_with_retry(
                    model=self.model,
                    messages=[{"role": "user", "content": update_prompt}],
                    tools=None, tool_choice=None,
                ),
                timeout=120,  # 2 minutes per category update
            )
            new_content = (response.content or "").strip()
            if new_content and len(new_content) > 100:
                # 确保以 --- 开头（YAML frontmatter）
                if not new_content.startswith("---"):
                    new_content = "---\n" + new_content
                cat_path.write_text(new_content, encoding="utf-8")
                logger.info("Dream Phase 2: updated {} ({} chars)", memory_type, len(new_content))
            else:
                # LLM 输出太短，只追加 items
                cat_path.write_text(updated, encoding="utf-8")
                logger.info("Dream Phase 2: appended items to {} (LLM output too short)", memory_type)
        except Exception:
            logger.exception("Dream Phase 2: LLM update failed for {}", memory_type)
            # fallback: 只追加 items
            cat_path.write_text(updated, encoding="utf-8")

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

    async def run(
        self,
        changelog: list[str] | None = None,
        new_items_by_type: dict[str, list[dict]] | None = None,
    ) -> bool:
        """Phase 2+3: 分类路由 + 规则生成 + memory.md 摘要更新。

        Args:
            changelog: Phase 1 输出的变更日志（来自 run_extraction）。
                若为 None 则使用上次 run_extraction 的缓存结果。
            new_items_by_type: Phase 1 输出的结构化 items（来自 run_extraction）。
                若为 None 则使用上次 run_extraction 的缓存结果。
        """
        # 优先使用显式参数，回退到 run_extraction 缓存
        if changelog is None:
            changelog = list(self._last_changelog)
            self._last_changelog = []
        else:
            changelog = list(changelog)
        if new_items_by_type is None:
            new_items_by_type = dict(self._last_items_by_type)
            self._last_items_by_type = {}
        else:
            new_items_by_type = dict(new_items_by_type)

        if not changelog:
            # 即使没有新 items，也确保 memory.md 存在
            if self._category_manager:
                memory_md_path = self._category_manager._memory_dir / "memory.md"
                if not memory_md_path.exists():
                    logger.info("Dream: no new items but memory.md missing, generating")
                    try:
                        await self._regenerate_memory_md_with_llm()
                    except Exception:
                        logger.exception("Dream: memory.md generation failed")
            return False

        logger.info("Dream Phase 2: processing {} changelog entries", len(changelog))

        # Log new_items_by_type summary
        for mt, items in new_items_by_type.items():
            logger.info("Dream Phase 2: {} has {} new items", mt, len(items))

        # ---- Phase 2b: 分类路由 + 三层更新（并发执行） ----
        builtin_types = {"preference", "knowledge", "profile", "compliance", "behavior"}
        update_tasks = []
        for mt in builtin_types:
            new_items = new_items_by_type.get(mt, [])
            # Also pull existing items from SQLite for this type
            existing_items = self._get_items_by_type(mt)
            # Merge: new items first, then existing (dedup by item_id)
            seen_ids = {it["item_id"] for it in new_items}
            merged = list(new_items)
            for it in existing_items:
                if it["item_id"] not in seen_ids:
                    merged.append(it)
                    seen_ids.add(it["item_id"])
            if merged:
                update_tasks.append((mt, merged, len(new_items)))

        custom_items = new_items_by_type.get("custom", [])
        existing_custom = self._get_items_by_type("custom")
        seen_ids = {it["item_id"] for it in custom_items}
        merged_custom = list(custom_items)
        for it in existing_custom:
            if it["item_id"] not in seen_ids:
                merged_custom.append(it)
                seen_ids.add(it["item_id"])
        if merged_custom:
            update_tasks.append(("custom", merged_custom, len(custom_items)))

        # 并发执行所有 category 更新（每个含一次 LLM 调用）
        if update_tasks:
            results = await asyncio.gather(
                *[self._update_category_structure(mt, items) for mt, items, _ in update_tasks],
                return_exceptions=True,
            )
            for (mt, items, new_count), result in zip(update_tasks, results):
                if isinstance(result, Exception):
                    logger.exception("Dream Phase 2: update {} failed", mt)
                else:
                    changelog.append(f"updated {mt}.md ({new_count} new, {len(items)} total)")

        # ---- Phase 2c: 从 confirmed patterns 生成规则 ----
        try:
            rules = await self._generate_rules_from_patterns()
            if rules:
                changelog.append(f"generated {len(rules)} rules from confirmed patterns")
        except Exception:
            logger.exception("Dream: rule generation from patterns failed (non-fatal)")

        # ---- Phase 2d: Cleanup stale custom categories ----
        try:
            archived = await self._cleanup_stale_custom_categories()
            if archived:
                changelog.append(f"archived {archived} stale custom categories")
        except Exception:
            logger.exception("Dream: custom category cleanup failed (non-fatal)")

        # ---- Phase 3: Regenerate memory.md ----
        try:
            await self._regenerate_memory_md_with_llm(changelog=changelog)
        except Exception:
            logger.exception("Dream Phase 3 (memory.md summary) failed")

        # Git auto-commit
        if changelog and self.store.git.is_initialized():
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            sha = self.store.git.auto_commit(f"dream: {ts}, {len(changelog)} change(s)")
            if sha:
                logger.info("Dream commit: {}", sha)

        logger.info("Dream done: {} change(s)", len(changelog))
        return True

    async def _generate_rules_from_patterns(self) -> list[dict]:
        """Phase 2: 从 confirmed patterns 生成动态规则，写入 dynamic_rules.jsonl。"""
        if not self._pattern_store or not self._dynamic_rule_store:
            return []

        confirmed = self._pattern_store.get_confirmed()
        if not confirmed:
            return []

        existing_rules = self._dynamic_rule_store.get_all()
        existing_triggers = {r.get("trigger", {}).get("pattern", "") for r in existing_rules}

        new_rules: list[dict] = []
        for pattern in confirmed:
            desc = pattern.get("description", "")
            if not desc or desc in existing_triggers:
                continue

            rule = {
                "rule_id": f"dyn_{pattern.get('pattern_id', 'unknown')}",
                "type": "predict",
                "trigger": {
                    "pattern": desc,
                    "conditions": [],
                },
                "action": {
                    "topic_template": desc,
                    "content_template": f"基于观察到的模式: {desc}",
                    "category": "insight",
                },
                "confidence": pattern.get("confidence", 0.5),
                "source": "dream_phase2",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
            }
            new_rules.append(rule)
            existing_triggers.add(desc)

        for rule in new_rules:
            try:
                self._dynamic_rule_store.add_rule(rule)
                if rule.get("type") == "predict" and self._prediction_engine:
                    self._prediction_engine.add_dynamic_rule(rule)
            except Exception:
                logger.debug("Failed to add rule: {}", rule.get("rule_id"))

        if new_rules:
            self._dynamic_rule_store.cleanup_expired()
            logger.info("Dream: generated {} rules from confirmed patterns", len(new_rules))

        return new_rules

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
