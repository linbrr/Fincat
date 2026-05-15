"""RealTimePreFilter — zero-LLM-cost real-time memory extraction.

Scans each conversation turn for financial entities and keyword matches,
writing high-confidence items directly to category markdown and queuing
medium-confidence items for Dream validation.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from fincat.agent.memory import MemoryStore
    from fincat.agent.memory_item import MemoryItemStore

_DEFAULT_KEYWORDS_PATH = Path(__file__).parent / "prefilter_keywords.json"
_CONTEXT_WINDOW = 30  # characters before/after a match to extract as context


class RealTimePreFilter:
    """Zero-LLM-cost real-time memory pre-filter.

    Uses keyword rules and regex patterns to extract high-value memories
    from conversation turns. High-confidence items are written directly
    to category markdown; medium-confidence items are queued for Dream.
    """

    def __init__(
        self,
        store: MemoryStore,
        item_store: MemoryItemStore | None = None,
        keywords_path: Path | None = None,
    ):
        self.store = store
        self.item_store = item_store
        self._keywords_path = keywords_path or _DEFAULT_KEYWORDS_PATH
        self._keywords_mtime: float = 0
        self._categories: dict[str, dict[str, list[str]]] = {}
        self._entity_patterns: dict[str, re.Pattern] = {}
        self._queue_path = store.memory_dir / ".prefilter_queue.jsonl"
        self._reload_keywords_if_changed()

    # -- public API ---------------------------------------------------------

    def process_turn(
        self,
        user_message: str,
        agent_response: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Scan one conversation turn, return extracted memory items.

        Returns list of {category, content, confidence, source}.
        """
        self._reload_keywords_if_changed()
        # Only scan user message — agent responses include tool output
        # (stock data tables, etc.) which would pollute long-term memory
        items: list[dict[str, Any]] = []
        items.extend(self._scan_entity_patterns(user_message))
        items.extend(self._scan_keywords(user_message))
        return self._deduplicate(items)

    def flush(self, items: list[dict[str, Any]], session_id: str) -> None:
        """Route items: high -> markdown + MemoryItemStore, medium -> queue."""
        for item in items:
            if item["confidence"] == "high":
                try:
                    self.store.append_category_entry(
                        category=item["category"],
                        content=item["content"],
                    )
                    if self.item_store:
                        self.item_store.add(
                            content=item["content"],
                            category=item["category"],
                            source_session=f"prefilter:{session_id}",
                        )
                    logger.debug(
                        "PreFilter high: cat={} content={}",
                        item["category"],
                        item["content"][:60],
                    )
                except Exception:
                    logger.exception(
                        "PreFilter write failed for category={}", item["category"]
                    )
            else:
                self._append_to_queue(item, session_id)

    # -- scanning layers ----------------------------------------------------

    def _scan_entity_patterns(self, text: str) -> list[dict[str, Any]]:
        """Layer 1: regex scan for financial entities (all high confidence)."""
        items: list[dict[str, Any]] = []
        for name, pattern in self._entity_patterns.items():
            for match in pattern.finditer(text):
                context = self._extract_context(text, match.start(), match.end())
                if not context:
                    continue
                # Stock codes and financial metrics -> knowledge
                # Amounts mentioning personal assets -> profile
                category = "profile" if name == "amount" else "knowledge"
                items.append({
                    "category": category,
                    "content": context,
                    "confidence": "high",
                    "source": f"entity:{name}",
                })
        return items

    def _scan_keywords(self, text: str) -> list[dict[str, Any]]:
        """Layer 2: keyword matching with high/medium confidence levels."""
        items: list[dict[str, Any]] = []
        for category, levels in self._categories.items():
            for kw in levels.get("high", []):
                idx = text.find(kw)
                if idx >= 0:
                    context = self._extract_context(text, idx, idx + len(kw))
                    if context:
                        items.append({
                            "category": category,
                            "content": context,
                            "confidence": "high",
                            "source": f"keyword:high:{kw}",
                        })
            for kw in levels.get("medium", []):
                idx = text.find(kw)
                if idx >= 0:
                    context = self._extract_context(text, idx, idx + len(kw))
                    if context:
                        items.append({
                            "category": category,
                            "content": context,
                            "confidence": "medium",
                            "source": f"keyword:medium:{kw}",
                        })
        return items

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _extract_context(text: str, start: int, end: int, window: int = _CONTEXT_WINDOW) -> str:
        """Extract surrounding context around a match for meaningful storage."""
        ctx_start = max(0, start - window)
        ctx_end = min(len(text), end + window)
        snippet = text[ctx_start:ctx_end].strip()
        # Clean up leading/trailing punctuation and whitespace
        snippet = re.sub(r'^[\s,，。、；：！？\-]+', '', snippet)
        snippet = re.sub(r'[\s,，。、；：！？\-]+$', '', snippet)
        return snippet if len(snippet) >= 4 else ""

    def _deduplicate(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate by (category, content), keeping higher confidence."""
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        confidence_order = {"high": 2, "medium": 1}
        for item in items:
            key = (item["category"], item["content"])
            existing = seen.get(key)
            if existing is None:
                seen[key] = item
            elif confidence_order.get(item["confidence"], 0) > confidence_order.get(
                existing["confidence"], 0
            ):
                seen[key] = item
        return list(seen.values())

    # -- queue I/O ----------------------------------------------------------

    def _append_to_queue(self, item: dict[str, Any], session_id: str) -> None:
        """Append a medium-confidence item to the prefilter queue file."""
        entry = {
            "id": str(uuid.uuid4())[:8],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": item["category"],
            "content": item["content"],
            "source": item.get("source", "prefilter"),
            "session_id": session_id,
        }
        try:
            self._queue_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._queue_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logger.debug(
                "PreFilter queued: cat={} content={}",
                item["category"],
                item["content"][:60],
            )
        except OSError:
            logger.warning("Failed to write prefilter queue to {}", self._queue_path)

    def read_queue(self) -> list[dict[str, Any]]:
        """Read all entries from the prefilter queue."""
        if not self._queue_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            with open(self._queue_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            logger.warning("Failed to read prefilter queue from {}", self._queue_path)
        return entries

    def clear_queue(self) -> None:
        """Clear the prefilter queue after Dream processing."""
        try:
            if self._queue_path.exists():
                self._queue_path.unlink()
                logger.debug("Prefilter queue cleared")
        except OSError:
            logger.warning("Failed to clear prefilter queue at {}", self._queue_path)

    def queue_size(self) -> int:
        """Return the number of entries in the prefilter queue."""
        if not self._queue_path.exists():
            return 0
        try:
            with open(self._queue_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    # -- keyword library loading --------------------------------------------

    def _reload_keywords_if_changed(self) -> None:
        """Hot-reload keyword library when the JSON file changes."""
        try:
            mtime = self._keywords_path.stat().st_mtime
            if mtime <= self._keywords_mtime:
                return
            with open(self._keywords_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._categories = data.get("categories", {})
            # Compile entity patterns
            self._entity_patterns = {}
            for name, pattern_str in data.get("entity_patterns", {}).items():
                try:
                    self._entity_patterns[name] = re.compile(pattern_str)
                except re.error:
                    logger.warning("Invalid regex pattern for entity '{}': {}", name, pattern_str)
            self._keywords_mtime = mtime
            total_kw = sum(
                len(v.get("high", [])) + len(v.get("medium", []))
                for v in self._categories.values()
            )
            logger.info(
                "PreFilter keywords loaded: {} categories, {} keywords, {} entity patterns",
                len(self._categories),
                total_kw,
                len(self._entity_patterns),
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load prefilter keywords from {}: {}", self._keywords_path, exc)
