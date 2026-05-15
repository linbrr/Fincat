"""MemoryWakeupLog — memory system internal black-box recorder.

Records every memory retrieval event with full pipeline timing,
matched results, and outcome signals (used_flag, user_feedback).

Complements interaction_logs (user-facing) with system-internal metrics:
- Pipeline timing: filter → read → inject → LLM usage
- Matched categories and items with scores
- Outcome tracking: did LLM use the memory? Did user find it useful?

Use cases:
1. Offline mining of proactive prediction rules
2. Auto-tune retrieval accuracy (reduce useless injections)
3. Optimize intent filter (reduce wasted retrievals)
4. Performance profiling (where is memory time spent?)
5. Proactive prediction effectiveness feedback
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class MemoryWakeupRecord:
    """One memory retrieval event record."""
    wakeup_id: str = ""
    timestamp: str = ""
    trigger_type: str = "user_query"       # user_query | proactive_prediction | dream | periodic_scan
    query: str = ""                        # user message (truncated)
    session_id: str = ""

    # Pipeline timing (ms)
    filter_time_ms: int = 0                # pre-filter decision time
    query_embedding_time_ms: int = 0       # query → vector time
    category_match_time_ms: int = 0        # category vector search time
    item_retrieve_time_ms: int = 0         # FAISS item search + ranking time
    retrieval_pipeline_time_ms: int = 0    # total retrieval pipeline time
    memory_read_time_ms: int = 0           # memory.md read time
    context_build_time_ms: int = 0         # system prompt build time
    total_cost_time_ms: int = 0            # total memory overhead

    # Filter decision
    filter_skip: bool = False              # True = memory skipped entirely
    filter_reason: str = ""                # intent | blacklist | cache_hit | full_retrieval

    # Memory content (what was injected)
    memory_chars: int = 0                  # chars of memory.md injected
    memory_sections: list[str] = field(default_factory=list)  # section names in memory.md

    # Matched results (for future vector search)
    matched_categories: list[dict] = field(default_factory=list)  # [{cate_id, score}]
    matched_items: list[dict] = field(default_factory=list)       # [{item_id, score}]

    # Outcome signals
    used_flag: int = -1                    # 1=LLM used memory, 0=not used, -1=unknown
    user_feedback: int = 0                 # 1=positive, -1=negative, 0=neutral/no signal
    feedback_detail: str = ""              # what user said about the response

    # Metadata
    model: str = ""
    tools_used: list[str] = field(default_factory=list)
    response_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "wakeup_id": self.wakeup_id,
            "timestamp": self.timestamp,
            "trigger_type": self.trigger_type,
            "query": self.query[:200],
            "session_id": self.session_id,
            "filter_time_ms": self.filter_time_ms,
            "query_embedding_time_ms": self.query_embedding_time_ms,
            "category_match_time_ms": self.category_match_time_ms,
            "item_retrieve_time_ms": self.item_retrieve_time_ms,
            "retrieval_pipeline_time_ms": self.retrieval_pipeline_time_ms,
            "memory_read_time_ms": self.memory_read_time_ms,
            "context_build_time_ms": self.context_build_time_ms,
            "total_cost_time_ms": self.total_cost_time_ms,
            "filter_skip": self.filter_skip,
            "filter_reason": self.filter_reason,
            "memory_chars": self.memory_chars,
            "memory_sections": self.memory_sections,
            "matched_categories": self.matched_categories,
            "matched_items": self.matched_items,
            "used_flag": self.used_flag,
            "user_feedback": self.user_feedback,
            "feedback_detail": self.feedback_detail[:200],
            "model": self.model,
            "tools_used": self.tools_used,
            "response_length": self.response_length,
        }


class MemoryWakeupLogger:
    """Persists MemoryWakeupRecords to JSONL and provides analytics.

    Usage:
        logger = MemoryWakeupLogger(logs_dir)

        # Start a retrieval event
        record = logger.begin(query="三亚旅行预算", session_id="s1", trigger_type="user_query")

        # ... run pipeline, update record fields ...
        record.filter_skip = False
        record.memory_chars = 1200

        # Persist
        logger.finish(record)
    """

    def __init__(self, logs_dir: Path):
        self._dir = logs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = logs_dir / "memory_wakeup_logs.jsonl"

    def begin(
        self,
        query: str = "",
        session_id: str = "",
        trigger_type: str = "user_query",
    ) -> MemoryWakeupRecord:
        """Start a new wakeup record."""
        return MemoryWakeupRecord(
            wakeup_id=f"wakeup_{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            trigger_type=trigger_type,
            query=query,
            session_id=session_id,
        )

    def finish(self, record: MemoryWakeupRecord) -> None:
        """Persist the record to JSONL."""
        record.total_cost_time_ms = (
            record.filter_time_ms
            + record.retrieval_pipeline_time_ms
            + record.memory_read_time_ms
            + record.context_build_time_ms
        )
        self._append(record.to_dict())
        logger.debug(
            "MemoryWakeupLog: id={} skip={} reason={} mem_chars={} total_ms={}",
            record.wakeup_id, record.filter_skip, record.filter_reason,
            record.memory_chars, record.total_cost_time_ms,
        )

    def mark_used(self, wakeup_id: str, used: bool) -> None:
        """Update used_flag for a record (post-hoc, after LLM response)."""
        self._update_field(wakeup_id, "used_flag", 1 if used else 0)

    def mark_feedback(self, wakeup_id: str, feedback: int, detail: str = "") -> None:
        """Record user feedback for a wakeup event."""
        self._update_field(wakeup_id, "user_feedback", feedback)
        if detail:
            self._update_field(wakeup_id, "feedback_detail", detail[:200])

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_recent(self, hours: int = 24) -> list[dict]:
        """Get wakeup records from the last N hours."""
        cutoff = (
            datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=hours)
        ).isoformat()
        results = []
        for rec in self._read_all():
            if rec.get("timestamp", "") >= cutoff:
                results.append(rec)
        return results

    def get_stats(self, hours: int = 24) -> dict[str, Any]:
        """Aggregate statistics for the last N hours."""
        records = self.get_recent(hours)
        if not records:
            return {"total": 0}

        total = len(records)
        skipped = sum(1 for r in records if r.get("filter_skip"))
        used = sum(1 for r in records if r.get("used_flag") == 1)
        not_used = sum(1 for r in records if r.get("used_flag") == 0)
        positive_fb = sum(1 for r in records if r.get("user_feedback", 0) > 0)
        negative_fb = sum(1 for r in records if r.get("user_feedback", 0) < 0)

        avg_filter_ms = _avg(r.get("filter_time_ms", 0) for r in records)
        avg_read_ms = _avg(r.get("memory_read_time_ms", 0) for r in records)
        avg_total_ms = _avg(r.get("total_cost_time_ms", 0) for r in records)

        # Filter reason breakdown
        reasons: dict[str, int] = {}
        for r in records:
            reason = r.get("filter_reason", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1

        # Trigger type breakdown
        triggers: dict[str, int] = {}
        for r in records:
            tt = r.get("trigger_type", "unknown")
            triggers[tt] = triggers.get(tt, 0) + 1

        return {
            "total": total,
            "skipped": skipped,
            "skip_rate": skipped / total,
            "used": used,
            "not_used": not_used,
            "usage_rate": used / max(1, used + not_used),
            "positive_feedback": positive_fb,
            "negative_feedback": negative_fb,
            "avg_filter_ms": round(avg_filter_ms, 1),
            "avg_read_ms": round(avg_read_ms, 1),
            "avg_total_ms": round(avg_total_ms, 1),
            "reason_breakdown": reasons,
            "trigger_breakdown": triggers,
        }

    def get_slow_queries(self, threshold_ms: int = 50, limit: int = 20) -> list[dict]:
        """Find queries where memory overhead exceeded threshold."""
        results = []
        for rec in self._read_all():
            if rec.get("total_cost_time_ms", 0) > threshold_ms:
                results.append(rec)
                if len(results) >= limit:
                    break
        return results

    def get_unused_injections(self, limit: int = 50) -> list[dict]:
        """Find memory injections that LLM didn't use (wasted context)."""
        results = []
        for rec in self._read_all():
            if not rec.get("filter_skip") and rec.get("used_flag") == 0:
                results.append(rec)
                if len(results) >= limit:
                    break
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        results = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results

    def _update_field(self, wakeup_id: str, field: str, value: Any) -> None:
        """Update a field in an existing record by wakeup_id."""
        if not self._path.exists():
            return
        lines = self._path.read_text(encoding="utf-8").splitlines()
        updated = False
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("wakeup_id") == wakeup_id:
                rec[field] = value
                lines[i] = json.dumps(rec, ensure_ascii=False)
                updated = True
                break
        if updated:
            self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _avg(values) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------

class Timer:
    """Simple millisecond timer context manager."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)
