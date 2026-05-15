"""SQLite-based tiered memory store for financial agent.

This module implements a hybrid memory system combining:
- FTS5 (Full-Text Search) for keyword-based search
- sqlite-vec for semantic/vector search

Storage tiers:
- Warm Memory (SQLite): assets, transactions, active_tasks
- Cold Memory (SQLite + vec): user_profile, reflection_vault, knowledge_base
- Memory Index (SQLite + FTS5): derived index from category Markdown files
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

# Optional sqlite-vec import (will fall back to pure FTS5 if not available)
try:
    import sqlite_vec

    VEC_AVAILABLE = True
except ImportError:
    VEC_AVAILABLE = False
    logger.warning("sqlite-vec not available, vector search disabled")

# Category → filename mapping (mirrors MemoryStore.CATEGORY_FILES)
_CATEGORY_FILE_MAP = {
    "preference": "user_preferences.md",
    "knowledge": "product_knowledge.md",
    "case": "conversation_cases.md",
    "compliance": "compliance_rules.md",
    "profile": "user_profile.md",
    "insight": "behavioral_insights.md",
}

_CATEGORY_FILE_MAP_REVERSE = {v: k for k, v in _CATEGORY_FILE_MAP.items()}

# ============================================================================
# Database Schema
# ============================================================================

SCHEMA_SQL = """
-- Assets: current positions
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    quantity REAL NOT NULL,
    cost_basis REAL NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Transactions: historical trades
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    total_amount REAL NOT NULL,
    decision_reason TEXT,
    notes TEXT,
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Active tasks: ongoing operations
CREATE TABLE IF NOT EXISTS active_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL DEFAULT 0.0,
    result TEXT,
    error TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

-- User profile: key-value store
CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    updated_at TEXT NOT NULL
);

-- Knowledge base: documents, research, strategies
CREATE TABLE IF NOT EXISTS knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    source_url TEXT,
    tags TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Reflection vault: lessons learned
CREATE TABLE IF NOT EXISTS reflection_vault (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    embedding BLOB,
    tags TEXT,
    source TEXT,
    reflection_type TEXT DEFAULT 'general',
    created_at TEXT NOT NULL
);
"""

# sqlite-vec schema (only if available)
VEC_SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS reflection_vault_vec USING vec0(
    embedding REAL[768],
    content TEXT,
    reflection_id INTEGER
);
"""

# Memory index schema — derived index from category Markdown files
MEMORY_INDEX_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content_preview TEXT,
    tags TEXT,
    md5_hash TEXT NOT NULL,
    file_path TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_index_fts USING fts5(
    title,
    content_preview,
    tags,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    file_path TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    decay_score REAL DEFAULT 1.0,
    confidence REAL DEFAULT 0.5,
    entities TEXT,
    md5_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_entries_fts USING fts5(
    content,
    category,
    tokenize='unicode61'
);
"""


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class Asset:
    """Represents a current position/asset."""

    symbol: str
    quantity: float
    cost_basis: float
    updated_at: str
    id: int | None = None
    created_at: str | None = None

    @property
    def avg_price(self) -> float:
        return self.cost_basis / self.quantity if self.quantity > 0 else 0.0


@dataclass
class Transaction:
    """Represents a historical trade transaction."""

    symbol: str
    action: str  # "BUY" or "SELL"
    quantity: float
    price: float
    total_amount: float
    timestamp: str
    decision_reason: str | None = None
    notes: str | None = None
    id: int | None = None
    created_at: str | None = None


@dataclass
class ActiveTask:
    """Represents an ongoing task."""

    task_id: str
    task_type: str
    status: str = "pending"
    progress: float = 0.0
    result: str | None = None
    error: str | None = None
    metadata: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    id: int | None = None


@dataclass
class Reflection:
    """A reflection/lesson learned."""

    content: str
    tags: list[str] | None = None
    source: str | None = None
    reflection_type: str = "general"
    embedding: list[float] | None = None
    id: int | None = None
    created_at: str | None = None


@dataclass
class KnowledgeEntry:
    """A knowledge base entry."""

    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    tags: list[str] | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class MemoryIndexEntry:
    """An entry in the memory index (derived from category Markdown files)."""

    category: str
    title: str
    md5_hash: str
    file_path: str
    updated_at: str
    content_preview: str | None = None
    tags: list[str] | None = None
    id: int | None = None


@dataclass
class MemoryEntry:
    """A single memory entry indexed at item level from category Markdown files."""

    entry_id: str
    category: str
    content: str
    file_path: str
    md5_hash: str
    created_at: str
    updated_at: str
    frequency: int = 1
    decay_score: float = 1.0
    confidence: float = 0.5
    entities: list[str] | None = None
    id: int | None = None


# ============================================================================
# SQLiteMemoryStore
# ============================================================================


class SQLiteMemoryStore:
    """Tiered memory store using SQLite.

    Usage:
        store = SQLiteMemoryStore("~/.fincat/memory.db")

        # Assets
        store.upsert_asset("AAPL", quantity=100, cost_basis=15000)
        assets = store.get_all_assets()

        # Transactions
        store.add_transaction(symbol="AAPL", action="BUY", quantity=10, price=150, ...)
        txs = store.search_transactions("AAPL", days=30)

        # Reflections
        store.add_reflection("某次追高失败")
        results = store.search_reflections(query_text="追高", top_k=5)

        # Knowledge base
        results = store.search_knowledge("科技股")
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            if VEC_AVAILABLE:
                try:
                    conn.executescript(VEC_SCHEMA_SQL)
                except Exception as e:
                    logger.warning("sqlite-vec schema init failed: {}", e)
            try:
                conn.executescript(MEMORY_INDEX_SCHEMA_SQL)
            except Exception as e:
                logger.warning("memory index schema init failed: {}", e)
            conn.commit()
        logger.info("SQLiteMemoryStore initialized at {}", self.db_path)

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection (context manager)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------------
    # Assets (Warm Memory)
    # ------------------------------------------------------------------------

    def upsert_asset(
        self,
        symbol: str,
        quantity: float,
        cost_basis: float,
    ) -> Asset:
        """Insert or update an asset position."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM assets WHERE symbol = ?", (symbol,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE assets
                       SET quantity = ?, cost_basis = ?, updated_at = ?
                       WHERE symbol = ?""",
                    (quantity, cost_basis, now, symbol),
                )
                asset_id = existing["id"]
            else:
                conn.execute(
                    """INSERT INTO assets (symbol, quantity, cost_basis, updated_at, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (symbol, quantity, cost_basis, now, now),
                )
                asset_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

        return Asset(
            id=asset_id,
            symbol=symbol,
            quantity=quantity,
            cost_basis=cost_basis,
            updated_at=now,
        )

    def get_asset(self, symbol: str) -> Asset | None:
        """Get current asset by symbol."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT id, symbol, quantity, cost_basis, updated_at, created_at FROM assets WHERE symbol = ?", (symbol,)
            ).fetchone()
        if not row:
            return None
        return Asset(**dict(row))

    def get_all_assets(self) -> list[Asset]:
        """Get all current assets."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, symbol, quantity, cost_basis, updated_at, created_at FROM assets ORDER BY symbol"
            ).fetchall()
        return [Asset(**dict(row)) for row in rows]

    def delete_asset(self, symbol: str) -> bool:
        """Delete an asset position."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM assets WHERE symbol = ?", (symbol,))
            conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------------
    # Transactions (Warm Memory)
    # ------------------------------------------------------------------------

    def add_transaction(
        self,
        symbol: str,
        action: str,
        quantity: float,
        price: float,
        decision_reason: str | None = None,
        notes: str | None = None,
        timestamp: str | None = None,
    ) -> Transaction:
        """Add a new transaction record."""
        now = datetime.now().isoformat()
        ts = timestamp or now
        total = quantity * price

        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO transactions
                   (symbol, action, quantity, price, total_amount, decision_reason, notes, timestamp, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, action, quantity, price, total, decision_reason, notes, ts, now),
            )
            tx_id = cursor.lastrowid
            conn.commit()

        return Transaction(
            id=tx_id,
            symbol=symbol,
            action=action,
            quantity=quantity,
            price=price,
            total_amount=total,
            decision_reason=decision_reason,
            notes=notes,
            timestamp=ts,
            created_at=now,
        )

    def get_transactions(
        self,
        symbol: str | None = None,
        action: str | None = None,
        days: int | None = None,
        limit: int = 100,
    ) -> list[Transaction]:
        """Get transactions with optional filters."""
        query = "SELECT * FROM transactions WHERE 1=1"
        params: list[Any] = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if action:
            query += " AND action = ?"
            params.append(action)
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            query += " AND timestamp >= ?"
            params.append(cutoff)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [Transaction(**dict(row)) for row in rows]

    def search_transactions(
        self,
        query: str,
        symbol: str | None = None,
        days: int | None = None,
        limit: int = 20,
    ) -> list[Transaction]:
        """Search transactions by text using LIKE."""
        sql = "SELECT * FROM transactions WHERE 1=1"
        params: list[Any] = []

        if query:
            sql += " AND (decision_reason LIKE ? OR notes LIKE ? OR symbol LIKE ?)"
            like_pattern = f"%{query}%"
            params.extend([like_pattern, like_pattern, like_pattern])
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            sql += " AND timestamp >= ?"
            params.append(cutoff)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Transaction(**dict(row)) for row in rows]

    # ------------------------------------------------------------------------
    # Active Tasks (Warm Memory)
    # ------------------------------------------------------------------------

    def create_task(self, task_id: str, task_type: str, metadata: dict | None = None) -> ActiveTask:
        """Create a new active task."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO active_tasks (task_id, task_type, status, metadata, created_at, updated_at)
                   VALUES (?, ?, 'pending', ?, ?, ?)""",
                (task_id, task_type, json.dumps(metadata) if metadata else None, now, now),
            )
            task_db_id = cursor.lastrowid
            conn.commit()

        return ActiveTask(
            id=task_db_id,
            task_id=task_id,
            task_type=task_type,
            status="pending",
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )

    def update_task(
        self,
        task_id: str,
        status: str | None = None,
        progress: float | None = None,
        result: str | None = None,
        error: str | None = None,
    ) -> ActiveTask | None:
        """Update task status/progress."""
        now = datetime.now().isoformat()
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if result is not None:
            updates.append("result = ?")
            params.append(result)
        if error is not None:
            updates.append("error = ?")
            params.append(error)

        params.append(task_id)
        with self.get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE active_tasks SET {', '.join(updates)} WHERE task_id = ?",
                params,
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM active_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return ActiveTask(**dict(row)) if row else None

    def get_task(self, task_id: str) -> ActiveTask | None:
        """Get task by ID."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM active_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return ActiveTask(**dict(row))

    def get_active_tasks(self, task_type: str | None = None) -> list[ActiveTask]:
        """Get all active (non-completed) tasks."""
        query = "SELECT * FROM active_tasks WHERE status NOT IN ('completed', 'failed', 'cancelled')"
        if task_type:
            query += " AND task_type = ?"
        with self.get_connection() as conn:
            rows = conn.execute(query, (task_type,) if task_type else ()).fetchall()
        return [ActiveTask(**dict(row)) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM active_tasks WHERE task_id = ?", (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------------
    # User Profile (Cold Memory)
    # ------------------------------------------------------------------------

    def set_profile(self, key: str, value: str, category: str = "general") -> None:
        """Set a user profile value."""
        now = datetime.now().isoformat()
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO user_profile (key, value, category, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, category, now),
            )
            conn.commit()

    def get_profile(self, key: str) -> str | None:
        """Get a user profile value."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT value FROM user_profile WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def get_all_profile_keys(self, category: str | None = None) -> dict[str, str]:
        """Get all profile key-value pairs, optionally filtered by category."""
        query = "SELECT key, value FROM user_profile"
        if category:
            query += " WHERE category = ?"
        with self.get_connection() as conn:
            rows = conn.execute(query, (category,) if category else ()).fetchall()
        return {row["key"]: row["value"] for row in rows}

    def search_profile(self, query: str, limit: int = 10) -> list[tuple[str, str]]:
        """Search user profile using LIKE."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """SELECT key, value FROM user_profile
                   WHERE value LIKE ? OR key LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [(row["key"], row["value"]) for row in rows]

    # ------------------------------------------------------------------------
    # Knowledge Base (Cold Memory)
    # ------------------------------------------------------------------------

    def add_knowledge(
        self,
        title: str,
        content: str,
        source: str | None = None,
        source_url: str | None = None,
        tags: list[str] | None = None,
    ) -> KnowledgeEntry:
        """Add a knowledge base entry."""
        now = datetime.now().isoformat()
        tags_str = ",".join(tags) if tags else None
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO knowledge_base (title, content, source, source_url, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, content, source, source_url, tags_str, now, now),
            )
            kb_id = cursor.lastrowid
            conn.commit()

        return KnowledgeEntry(
            id=kb_id,
            title=title,
            content=content,
            source=source,
            source_url=source_url,
            tags=tags,
            created_at=now,
            updated_at=now,
        )

    def search_knowledge(
        self,
        query: str,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[KnowledgeEntry]:
        """Search knowledge base using LIKE."""
        sql = "SELECT * FROM knowledge_base WHERE 1=1"
        params: list[Any] = []

        if query:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            like_pattern = f"%{query}%"
            params.extend([like_pattern, like_pattern])

        if tags:
            for tag in tags:
                sql += " AND tags LIKE ?"
                params.append(f"%{tag}%")

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        entries = []
        for row in rows:
            entry = KnowledgeEntry(**dict(row))
            entry.tags = row["tags"].split(",") if row["tags"] else None
            entries.append(entry)
        return entries

    def get_knowledge(self, kb_id: int) -> KnowledgeEntry | None:
        """Get knowledge entry by ID."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM knowledge_base WHERE id = ?", (kb_id,)).fetchone()
        if not row:
            return None
        entry = KnowledgeEntry(**dict(row))
        entry.tags = row["tags"].split(",") if row["tags"] else None
        return entry

    # ------------------------------------------------------------------------
    # Reflection Vault (Cold Memory + Vector Search)
    # ------------------------------------------------------------------------

    def add_reflection(
        self,
        content: str,
        embedding: list[float] | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
        reflection_type: str = "general",
    ) -> Reflection:
        """Add a reflection/lesson learned.

        If sqlite-vec is available and embedding is provided, also store vector.
        """
        now = datetime.now().isoformat()
        tags_str = ",".join(tags) if tags else None

        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO reflection_vault (content, embedding, tags, source, reflection_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (content, json.dumps(embedding) if embedding else None, tags_str, source, reflection_type, now),
            )
            refl_id = cursor.lastrowid
            conn.commit()

            # Store vector in sqlite-vec if available
            if VEC_AVAILABLE and embedding:
                try:
                    embedding_bytes = self._embeddings_to_bytes([embedding])
                    conn.execute(
                        """INSERT INTO reflection_vault_vec (embedding, content, reflection_id)
                           VALUES (?, ?, ?)""",
                        (embedding_bytes, content, refl_id),
                    )
                    conn.commit()
                except Exception as e:
                    logger.warning("Failed to store vector: {}", e)

        return Reflection(
            id=refl_id,
            content=content,
            embedding=embedding,
            tags=tags,
            source=source,
            reflection_type=reflection_type,
            created_at=now,
        )

    def search_reflections(
        self,
        query_embedding: list[float] | None = None,
        query_text: str | None = None,
        reflection_type: str | None = None,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[Reflection]:
        """Search reflections.

        Uses sqlite-vec for semantic search if embedding provided,
        falls back to text search, or returns all if neither.
        """
        if query_embedding and VEC_AVAILABLE:
            return self._search_reflections_vector(query_embedding, reflection_type, tags, top_k)
        elif query_text:
            return self._search_reflections_text(query_text, reflection_type, tags, top_k)
        else:
            return self._get_all_reflections(reflection_type, tags, top_k)

    def _search_reflections_vector(
        self,
        query_embedding: list[float],
        reflection_type: str | None = None,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[Reflection]:
        """Search reflections using vector similarity (sqlite-vec)."""
        try:
            embedding_bytes = self._embeddings_to_bytes([query_embedding])
            with self.get_connection() as conn:
                sql = """
                    SELECT r.*, vec.distance
                    FROM reflection_vault_vec v
                    JOIN reflection_vault r ON v.reflection_id = r.id
                    WHERE v.embedding MATCH ?
                """
                params: list[Any] = [embedding_bytes]

                if reflection_type:
                    sql += " AND r.reflection_type = ?"
                    params.append(reflection_type)
                if tags:
                    for tag in tags:
                        sql += " AND r.tags LIKE ?"
                        params.append(f"%{tag}%")

                sql += " ORDER BY vec.distance LIMIT ?"
                params.append(top_k)

                rows = conn.execute(sql, params).fetchall()

            reflections = []
            for row in rows:
                refl = Reflection(**{k: row[k] for k in row.keys() if k != "distance"})
                refl.tags = row["tags"].split(",") if row["tags"] else None
                reflections.append(refl)
            return reflections
        except Exception as e:
            logger.warning("Vector search failed, falling back to text: {}", e)
            return self._search_reflections_text("", reflection_type, tags, top_k)

    def _search_reflections_text(
        self,
        query: str,
        reflection_type: str | None = None,
        tags: list[str] | None = None,
        top_k: int = 5,
    ) -> list[Reflection]:
        """Search reflections using text search."""
        sql = "SELECT * FROM reflection_vault WHERE 1=1"
        params: list[Any] = []

        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")
        if reflection_type:
            sql += " AND reflection_type = ?"
            params.append(reflection_type)
        if tags:
            for tag in tags:
                sql += " AND tags LIKE ?"
                params.append(f"%{tag}%")

        sql += f" ORDER BY created_at DESC LIMIT {top_k}"

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        reflections = []
        for row in rows:
            refl = Reflection(**dict(row))
            refl.tags = row["tags"].split(",") if row["tags"] else None
            reflections.append(refl)
        return reflections

    def _get_all_reflections(
        self,
        reflection_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[Reflection]:
        """Get all reflections without search."""
        sql = "SELECT * FROM reflection_vault WHERE 1=1"
        params: list[Any] = []

        if reflection_type:
            sql += " AND reflection_type = ?"
            params.append(reflection_type)
        if tags:
            for tag in tags:
                sql += " AND tags LIKE ?"
                params.append(f"%{tag}%")

        sql += f" ORDER BY created_at DESC LIMIT {limit}"

        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        reflections = []
        for row in rows:
            refl = Reflection(**dict(row))
            refl.tags = row["tags"].split(",") if row["tags"] else None
            reflections.append(refl)
        return reflections

    def get_reflections_by_tag(self, tag: str, reflection_type: str | None = None) -> list[Reflection]:
        """Get all reflections that have a specific tag.

        Used by SkillDeduplicator to retrieve existing task_reflection entries
        for similarity comparison.
        """
        return self._get_all_reflections(reflection_type=reflection_type, tags=[tag], limit=100)

    @staticmethod
    def _embeddings_to_bytes(embeddings: list[list[float]]) -> bytes:
        """Convert list of embedding lists to sqlite-vec binary format."""
        import struct

        floats = [f for emb in embeddings for f in emb]
        return struct.pack(f"<{len(floats)}f", *floats)

    @staticmethod
    def _bytes_to_embeddings(data: bytes, dim: int) -> list[list[float]]:
        """Convert sqlite-vec binary format back to embedding lists."""
        import struct

        count = len(data) // (dim * 4)
        embeddings = []
        for i in range(count):
            start = i * dim * 4
            end = start + dim * 4
            embeddings.append(list(struct.unpack(f"<{dim}f", data[start:end])))
        return embeddings

    # ------------------------------------------------------------------------
    # Memory Index (derived from category Markdown files)
    # ------------------------------------------------------------------------

    def sync_from_markdown(self, memory_dir: str | Path) -> int:
        """Incrementally sync category Markdown files into the memory index.

        Reads all category .md files (excluding MEMORY.md), extracts H1/H2
        headings as titles and first paragraph as content_preview. Only updates
        entries whose md5 hash has changed.

        Returns the number of index entries updated.
        """
        memory_dir = Path(memory_dir)
        if not memory_dir.exists():
            return 0

        updated = 0
        now = datetime.now().isoformat()

        for md_file in sorted(memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue

            content = md_file.read_text(encoding="utf-8")
            file_hash = hashlib.md5(content.encode()).hexdigest()

            # Check existing entry
            with self.get_connection() as conn:
                row = conn.execute(
                    "SELECT id, md5_hash FROM memory_index WHERE file_path = ?",
                    (str(md_file),),
                ).fetchone()

                if row and row["md5_hash"] == file_hash:
                    continue  # unchanged

                # Extract metadata from markdown
                title = self._extract_md_title(content, md_file)
                preview = self._extract_md_preview(content)
                tags = self._extract_md_tags(content)
                category = self._infer_category(md_file.name)

                if row:
                    conn.execute(
                        """UPDATE memory_index
                           SET title=?, content_preview=?, tags=?, md5_hash=?, updated_at=?
                           WHERE id=?""",
                        (title, preview, tags, file_hash, now, row["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO memory_index
                           (category, title, content_preview, tags, md5_hash, file_path, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (category, title, preview, tags, file_hash, str(md_file), now),
                    )

                # Sync FTS5 index
                idx_id = row["id"] if row else conn.execute(
                    "SELECT id FROM memory_index WHERE file_path = ?", (str(md_file),)
                ).fetchone()["id"]
                conn.execute("DELETE FROM memory_index_fts WHERE rowid = ?", (idx_id,))
                conn.execute(
                    "INSERT INTO memory_index_fts (rowid, title, content_preview, tags) VALUES (?, ?, ?, ?)",
                    (idx_id, title, preview, tags or ""),
                )
                conn.commit()
                updated += 1

        if updated:
            logger.info("Memory index sync: {} file(s) updated", updated)
        return updated

    def rebuild_index(self, memory_dir: str | Path) -> int:
        """Drop and rebuild the entire memory index from Markdown files.

        Returns the number of index entries created.
        """
        memory_dir = Path(memory_dir)
        with self.get_connection() as conn:
            conn.execute("DELETE FROM memory_index_fts")
            conn.execute("DELETE FROM memory_index")
            conn.commit()

        if not memory_dir.exists():
            return 0

        count = 0
        now = datetime.now().isoformat()

        for md_file in sorted(memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue

            content = md_file.read_text(encoding="utf-8")
            file_hash = hashlib.md5(content.encode()).hexdigest()
            title = self._extract_md_title(content, md_file)
            preview = self._extract_md_preview(content)
            tags = self._extract_md_tags(content)
            category = self._infer_category(md_file.name)

            with self.get_connection() as conn:
                cursor = conn.execute(
                    """INSERT INTO memory_index
                       (category, title, content_preview, tags, md5_hash, file_path, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (category, title, preview, tags, file_hash, str(md_file), now),
                )
                idx_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO memory_index_fts (rowid, title, content_preview, tags) VALUES (?, ?, ?, ?)",
                    (idx_id, title, preview, tags or ""),
                )
                conn.commit()
                count += 1

        logger.info("Memory index rebuilt: {} file(s) indexed", count)
        return count

    def search_memory(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
    ) -> list[MemoryIndexEntry]:
        """Full-text search over category Markdown files using FTS5.

        Returns matching MemoryIndexEntry objects ranked by relevance.
        """
        with self.get_connection() as conn:
            try:
                sql = """
                    SELECT mi.* FROM memory_index_fts fts
                    JOIN memory_index mi ON mi.id = fts.rowid
                    WHERE memory_index_fts MATCH ?
                """
                params: list[Any] = [query]

                if category:
                    sql += " AND mi.category = ?"
                    params.append(category)

                sql += " ORDER BY rank LIMIT ?"
                params.append(limit)

                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # FTS5 query syntax error — fall back to LIKE
                like_pattern = f"%{query}%"
                sql = """
                    SELECT * FROM memory_index
                    WHERE title LIKE ? OR content_preview LIKE ? OR tags LIKE ?
                """
                params = [like_pattern, like_pattern, like_pattern]
                if category:
                    sql += " AND category = ?"
                    params.append(category)
                sql += " LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()

        entries = []
        for row in rows:
            entry = MemoryIndexEntry(**dict(row))
            entry.tags = row["tags"].split(",") if row["tags"] else None
            entries.append(entry)
        return entries

    # -- Entry-level index (item granularity) ---------------------------------

    def sync_entries_from_markdown(self, memory_dir: str | Path) -> int:
        """Sync individual memory entries from category Markdown files.

        Parses <!-- item:... --> metadata blocks and indexes each entry
        separately with its own FTS5 record. Also extracts entities from
        content using regex patterns.

        Returns the number of entries updated.
        """
        import json as _json

        memory_dir = Path(memory_dir)
        if not memory_dir.exists():
            return 0

        # Entity extraction patterns (same as prefilter)
        entity_patterns = {
            "stock_code": re.compile(r"[036]\d{5}"),
            "financial_metric": re.compile(r"PE|PB|ROE|ROA|EPS|净利润|营收|毛利率|净利率|市盈率|市净率"),
        }

        def _extract_entities(text: str) -> list[str]:
            entities = []
            for name, pattern in entity_patterns.items():
                for m in pattern.finditer(text):
                    entities.append(m.group())
            return list(set(entities))

        # Parse entries from each category file
        entry_pattern = re.compile(
            r"<!--\s*item:(?P<item_id>\S+)\s*\|\s*ts:(?P<ts>[^|]+?)\s*\|\s*"
            r"freq:(?P<freq>[\d.]+)\s*\|\s*decay:(?P<decay>[\d.]+)\s*\|\s*"
            r"conf:(?P<conf>[\d.]+)\s*-->\n-\s*(?P<content>.+)",
            re.MULTILINE,
        )

        updated = 0
        now = datetime.now().isoformat()

        with self.get_connection() as conn:
            for category, filename in _CATEGORY_FILE_MAP.items():
                md_file = memory_dir / filename
                if not md_file.exists():
                    continue

                content = md_file.read_text(encoding="utf-8")
                file_path_str = str(md_file)

                for m in entry_pattern.finditer(content):
                    entry_id = m.group("item_id")
                    entry_content = m.group("content").strip()
                    freq = int(float(m.group("freq")))
                    decay = float(m.group("decay"))
                    conf = float(m.group("conf"))
                    entry_hash = hashlib.md5(entry_content.encode()).hexdigest()
                    entities = _extract_entities(entry_content)
                    entities_json = _json.dumps(entities, ensure_ascii=False)

                    # Check existing
                    existing = conn.execute(
                        "SELECT id, md5_hash FROM memory_entries WHERE entry_id = ?",
                        (entry_id,),
                    ).fetchone()

                    if existing and existing["md5_hash"] == entry_hash:
                        # Update frequency/decay/confidence even if content unchanged
                        conn.execute(
                            """UPDATE memory_entries
                               SET frequency=?, decay_score=?, confidence=?, updated_at=?
                               WHERE id=?""",
                            (freq, decay, conf, now, existing["id"]),
                        )
                        continue

                    if existing:
                        conn.execute(
                            """UPDATE memory_entries
                               SET content=?, category=?, frequency=?, decay_score=?,
                                   confidence=?, entities=?, md5_hash=?, file_path=?, updated_at=?
                               WHERE id=?""",
                            (entry_content, category, freq, decay, conf,
                             entities_json, entry_hash, file_path_str, now, existing["id"]),
                        )
                        fts_rowid = existing["id"]
                    else:
                        cursor = conn.execute(
                            """INSERT INTO memory_entries
                               (entry_id, category, content, file_path, frequency,
                                decay_score, confidence, entities, md5_hash, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (entry_id, category, entry_content, file_path_str,
                             freq, decay, conf, entities_json, entry_hash, now, now),
                        )
                        fts_rowid = cursor.lastrowid

                    # Sync FTS5
                    conn.execute(
                        "DELETE FROM memory_entries_fts WHERE rowid = ?", (fts_rowid,)
                    )
                    conn.execute(
                        "INSERT INTO memory_entries_fts (rowid, content, category) VALUES (?, ?, ?)",
                        (fts_rowid, entry_content, category),
                    )
                    updated += 1

            conn.commit()

        if updated:
            logger.info("Memory entries sync: {} entries updated", updated)
        return updated

    def search_memory_entries(
        self,
        query: str,
        category: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """FTS5 search over individual memory entries (not files)."""
        with self.get_connection() as conn:
            try:
                sql = """
                    SELECT me.* FROM memory_entries_fts fts
                    JOIN memory_entries me ON me.id = fts.rowid
                    WHERE memory_entries_fts MATCH ?
                """
                params: list[Any] = [query]
                if category:
                    sql += " AND me.category = ?"
                    params.append(category)
                if min_confidence > 0:
                    sql += " AND me.confidence >= ?"
                    params.append(min_confidence)
                sql += " ORDER BY rank LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                like_pattern = f"%{query}%"
                sql = """
                    SELECT * FROM memory_entries
                    WHERE content LIKE ?
                """
                params = [like_pattern]
                if category:
                    sql += " AND category = ?"
                    params.append(category)
                if min_confidence > 0:
                    sql += " AND confidence >= ?"
                    params.append(min_confidence)
                sql += " LIMIT ?"
                params.append(limit)
                rows = conn.execute(sql, params).fetchall()

        entries = []
        import json as _json
        for row in rows:
            d = dict(row)
            entities_raw = d.pop("entities", None)
            entry = MemoryEntry(**d)
            try:
                entry.entities = _json.loads(entities_raw) if entities_raw else None
            except (TypeError, _json.JSONDecodeError):
                entry.entities = None
            entries.append(entry)
        return entries

    @staticmethod
    def _extract_md_title(content: str, file_path: Path) -> str:
        """Extract the first H1 heading, or fall back to the filename stem."""
        m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if m:
            return m.group(1).strip()
        return file_path.stem.replace("_", " ").title()

    @staticmethod
    def _extract_md_preview(content: str) -> str | None:
        """Extract the first non-heading, non-empty paragraph as a preview."""
        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
                continue
            return stripped[:200]
        return None

    @staticmethod
    def _extract_md_tags(content: str) -> str | None:
        """Extract comma-separated tags from a '#tags: ...' line in Markdown."""
        m = re.search(r"^#\s*tags:\s*(.+)", content, re.MULTILINE | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _infer_category(filename: str) -> str:
        """Infer memory category from the filename."""
        return _CATEGORY_FILE_MAP_REVERSE.get(filename, "knowledge")

    # ------------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------------

    def vacuum(self) -> None:
        """Optimize database (run after bulk deletes)."""
        with self.get_connection() as conn:
            conn.execute("VACUUM")
            conn.commit()

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
