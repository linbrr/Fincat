"""SkillMemoryProvider — tracks recently written skills for cross-turn recall.

When a skill is created/updated via skill_manage, on_memory_write() records it.
On the next turn, prefetch() returns the skill name + description if relevant.

This gives the agent awareness of "the skill I just created last turn".
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from fincat.agent.memory_provider import MemoryProvider


# -- skill ledger schema ---------------------------------------------------

SKILL_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_memory_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    content    TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_ledger_name    ON skill_memory_ledger(skill_name);
CREATE INDEX IF NOT EXISTS idx_skill_ledger_created ON skill_memory_ledger(created_at);
"""


class SkillMemoryProvider(MemoryProvider):
    """Tracks recently created/updated skills for prefetch recall."""

    name = "skill_memory"

    def __init__(
        self,
        workspace: Path,
        db_path: Path | None = None,
    ):
        self.workspace = Path(workspace)
        self._skills_dir = self.workspace / "skills"
        self._memory_db = None

        # In-memory cache of recent skill writes (skill_name -> entry)
        self._recent: dict[str, dict[str, Any]] = {}

        if db_path:
            self._init_db(Path(db_path))

    def _init_db(self, db_path: Path) -> None:
        """Init skill ledger table in the memory db."""
        import sqlite3
        db_path = db_path.expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(db_path))
            conn.executescript(SKILL_LEDGER_SCHEMA)
            conn.close()
            self._memory_db = db_path
            logger.debug("SkillMemoryProvider ledger initialized at {}", db_path)
        except Exception as e:
            logger.warning("SkillMemoryProvider: failed to init ledger at {}: {}", db_path, e)

    # -- MemoryProvider -------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return recently written skills relevant to the query.

        Checks both in-memory cache and persisted ledger.
        Returns a short description of matching skills.
        """
        if not query:
            return ""

        query_lower = query.lower()

        # Collect recent entries (in-memory first, then from DB)
        entries = self._collect_recent_entries()

        # Filter entries whose skill matches the query
        matched: list[str] = []
        for skill_name, entry in entries.items():
            # Match by skill name keywords
            if self._matches_query(skill_name, query_lower):
                desc = entry.get("description", "")
                action = entry.get("action", "updated")
                matched.append(
                    f"- Skill '{skill_name}' ({action})"
                    + (f": {desc}" if desc else "")
                )
                continue

            # Match by description / content
            content = entry.get("content") or ""
            if content and query_lower in content.lower():
                matched.append(f"- Skill '{skill_name}' ({entry.get('action', 'updated')})")

        if not matched:
            return ""

        header = "# Recently Created/Updated Skills\n"
        return header + "\n".join(matched)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Record a skill write event for cross-turn recall."""
        if target != "skill":
            return

        skill_name = content.strip()
        if not skill_name:
            return

        # Load skill description if file exists
        description = ""
        skill_md_path = self._skills_dir / skill_name / "SKILL.md"
        if skill_md_path.exists():
            text = skill_md_path.read_text(encoding="utf-8")
            desc_match = re.search(
                r"^description:\s*[\"']?(.*?)[\"']?\s*$",
                text, re.MULTILINE
            )
            if desc_match:
                description = desc_match.group(1).strip()

        entry: dict[str, Any] = {
            "skill_name": skill_name,
            "action": action,
            "description": description,
            "content": description,
            "created_at": datetime.now().isoformat(),
        }

        # Update in-memory cache
        self._recent[skill_name] = entry

        # Persist to ledger DB if available
        if self._memory_db:
            try:
                import sqlite3
                conn = sqlite3.connect(str(self._memory_db))
                conn.execute(
                    """INSERT INTO skill_memory_ledger (skill_name, action, content, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (skill_name, action, description, entry["created_at"]),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning("SkillMemoryProvider: failed to persist: {}", e)

        logger.debug(
            "SkillMemoryProvider recorded: {} {} skill '{}'",
            action, target, skill_name,
        )

    # -- internal ------------------------------------------------------------

    def _matches_query(self, skill_name: str, query_lower: str) -> bool:
        """Check if skill name matches query keywords."""
        # Tokenize skill name: "stock-analysis" -> ["stock", "analysis"]
        tokens = set(re.split(r"[_\-]", skill_name.lower()))
        # Tokenize query words
        query_words = set(re.split(r"\W+", query_lower))
        return bool(tokens & query_words)

    def _collect_recent_entries(self) -> dict[str, dict[str, Any]]:
        """Collect recent skill entries from in-memory cache and DB (last 24h)."""
        result = dict(self._recent)

        # Also load recent entries from DB to catch any persisted ones
        if self._memory_db:
            try:
                import sqlite3
                cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
                conn = sqlite3.connect(str(self._memory_db))
                rows = conn.execute(
                    """SELECT skill_name, action, content, created_at
                       FROM skill_memory_ledger
                       WHERE created_at >= ?
                       ORDER BY created_at DESC""",
                    (cutoff,),
                ).fetchall()
                conn.close()
                for row in rows:
                    name, action, desc, created = row
                    if name not in result:
                        result[name] = {
                            "skill_name": name,
                            "action": action,
                            "description": desc or "",
                            "content": desc or "",
                            "created_at": created,
                        }
            except Exception as e:
                logger.warning("SkillMemoryProvider: failed to load recent from DB: {}", e)

        return result
