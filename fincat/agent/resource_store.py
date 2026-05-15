"""L1 Resource Store — conversations.jsonl + interaction_logs.jsonl."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger


class ResourceStore:
    """Append-only JSONL store for raw conversation records and interaction logs."""

    def __init__(self, resources_dir: Path):
        self._dir = resources_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._conv_path = resources_dir / "conversations.jsonl"
        self._log_path = resources_dir / "interaction_logs.jsonl"

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_conversation(
        self,
        content: str,
        metadata: dict,
        related_item_ids: list[str] | None = None,
    ) -> str:
        """Append one conversation turn. Returns resource_id."""
        resource_id = f"res_{uuid.uuid4().hex[:8]}"
        metadata.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        record = {
            "resource_id": resource_id,
            "type": "conversation",
            "content": content,
            "content_hash": _sha256(content),
            "metadata": metadata,
            "related_item_ids": related_item_ids or [],
        }
        self._append_jsonl(self._conv_path, record)
        return resource_id

    def add_interaction_log(
        self,
        category: str,
        action: str,
        metadata: dict | None = None,
        *,
        user_id: str = "user_default",
        session_id: str = "",
        content_digest: str = "",
        content_length: int = 0,
        response_time_ms: int = 0,
        associated_item_ids: list[str] | None = None,
        associated_category_ids: list[str] | None = None,
        feedback: int = 0,
        extra: dict | None = None,
    ) -> str:
        """Append one interaction log entry with enriched fields. Returns log_id."""
        log_id = f"log_{uuid.uuid4().hex[:8]}"
        record: dict[str, Any] = {
            "log_id": log_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "session_id": session_id or (metadata or {}).get("session_id", ""),
            "category": category,
            "action_type": action,
            "content_digest": content_digest,
            "content_length": content_length,
            "response_time_ms": response_time_ms,
            "associated_item_ids": associated_item_ids or [],
            "associated_category_ids": associated_category_ids or [],
            "feedback": feedback,
            "extra": extra or {},
        }
        # Merge legacy metadata fields for backward compatibility
        if metadata:
            for k, v in metadata.items():
                if k not in record and k != "session_id":
                    record[k] = v
        self._append_jsonl(self._log_path, record)
        return log_id

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_by_resource_id(self, resource_id: str) -> dict | None:
        for rec in self._read_jsonl(self._conv_path):
            if rec.get("resource_id") == resource_id:
                return rec
        return None

    def query_by_session(self, session_id: str) -> list[dict]:
        return [
            r for r in self._read_jsonl(self._conv_path)
            if r.get("metadata", {}).get("session_id") == session_id
        ]

    def query_by_timerange(self, start: str, end: str) -> list[dict]:
        results = []
        for rec in self._read_jsonl(self._conv_path):
            ts = rec.get("metadata", {}).get("timestamp", "")
            if start <= ts <= end:
                results.append(rec)
        return results

    def query_logs(
        self,
        category: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        results = []
        for rec in self._read_jsonl(self._log_path):
            if category and rec.get("category") != category:
                continue
            if action and rec.get("action_type", rec.get("action", "")) != action:
                continue
            results.append(rec)
            if len(results) >= limit:
                break
        return results

    def read_recent_conversations(self, hours: int = 24) -> list[dict]:
        cutoff = _hours_ago_iso(hours)
        return [
            r for r in self._read_jsonl(self._conv_path)
            if r.get("metadata", {}).get("timestamp", "") >= cutoff
        ]

    def read_recent_logs(self, hours: int = 24) -> list[dict]:
        cutoff = _hours_ago_iso(hours)
        return [r for r in self._read_jsonl(self._log_path) if r.get("timestamp", "") >= cutoff]

    def update_related_items(self, resource_id: str, item_ids: list[str]) -> None:
        """Back-fill related_item_ids after BatchExtractor creates items."""
        lines = self._conv_path.read_text(encoding="utf-8").splitlines()
        updated = False
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("resource_id") == resource_id:
                rec["related_item_ids"] = item_ids
                lines[i] = json.dumps(rec, ensure_ascii=False)
                updated = True
                break
        if updated:
            self._conv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def _rotate_if_needed(self, max_bytes: int = 10 * 1024 * 1024) -> None:
        """Rotate files when they exceed max_bytes (default 10MB)."""
        for path in (self._conv_path, self._log_path):
            if path.exists() and path.stat().st_size > max_bytes:
                ts = datetime.now().strftime("%Y_%m")
                archive = path.with_name(f"{path.stem}_{ts}.jsonl")
                path.rename(archive)
                logger.info("ResourceStore: rotated {} → {}", path.name, archive.name)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _append_jsonl(path: Path, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        if not path.exists():
            return []
        results = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                results.append(json.loads(line))
        return results


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _hours_ago_iso(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
