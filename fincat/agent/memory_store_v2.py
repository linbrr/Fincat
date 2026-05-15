"""L2 Memory Store — SQLite memory_item + FAISS vector index."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import faiss
import numpy as np
from loguru import logger

from fincat.agent.embedding import EmbeddingEngine

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class MemoryStoreV2:
    """L2 记忆层：SQLite memory_item 表 + FAISS 向量索引."""

    def __init__(self, db_path: Path, vector_dir: Path, embedding: EmbeddingEngine):
        self._db_path = db_path
        self._vector_dir = vector_dir
        self._vector_dir.mkdir(parents=True, exist_ok=True)
        self._embedding = embedding
        self._dimension = embedding.dimension

        # SQLite
        self._db = sqlite3.connect(str(db_path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

        # FAISS
        self._index_path = vector_dir / "item_vectors.index"
        self._faiss_index = self._load_or_create_faiss()

        # item_id ↔ faiss_index mapping (in-memory, synced from vector_mapping)
        self._id_to_idx: dict[str, int] = {}
        self._idx_to_id: dict[int, str] = {}
        self._load_mapping()

    # ------------------------------------------------------------------
    # Init helpers
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        if _SCHEMA_PATH.exists():
            self._db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        else:
            # Inline schema as fallback
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    resource_id  TEXT PRIMARY KEY,
                    content_hash TEXT,
                    timestamp    TEXT NOT NULL,
                    session_id   TEXT,
                    message_id   TEXT
                );
                CREATE TABLE IF NOT EXISTS memory_item (
                    item_id          TEXT PRIMARY KEY,
                    resource_id      TEXT,
                    category_id      TEXT,
                    memory_type      TEXT NOT NULL,
                    summary          TEXT NOT NULL,
                    content          TEXT,
                    importance_score REAL DEFAULT 0.5,
                    entities         TEXT DEFAULT '[]',
                    tags             TEXT DEFAULT '[]',
                    created_at       TEXT NOT NULL,
                    embedded_at      TEXT,
                    last_accessed_at TEXT,
                    access_count     INTEGER DEFAULT 0,
                    is_active        INTEGER DEFAULT 1,
                    extra            TEXT DEFAULT '{}'
                    -- resource_id references conversations.jsonl (not enforced via FK)
                );
                CREATE TABLE IF NOT EXISTS vector_mapping (
                    faiss_index  INTEGER PRIMARY KEY,
                    item_id      TEXT NOT NULL UNIQUE,
                    summary_hash TEXT,
                    embedded_at  TEXT NOT NULL,
                    model_name   TEXT NOT NULL DEFAULT 'bge-small-zh-v1.5'
                );
            """)
        self._db.commit()

    def _load_or_create_faiss(self) -> faiss.Index:
        if self._index_path.exists():
            return faiss.read_index(str(self._index_path))
        # Flat index (exact search), cosine similarity via inner product on normalized vectors
        return faiss.IndexFlatIP(self._dimension)

    def _load_mapping(self) -> None:
        cursor = self._db.execute("SELECT faiss_index, item_id FROM vector_mapping")
        for row in cursor:
            self._id_to_idx[row["item_id"]] = row["faiss_index"]
            self._idx_to_id[row["faiss_index"]] = row["item_id"]

    # ------------------------------------------------------------------
    # Item CRUD
    # ------------------------------------------------------------------

    def add_item(
        self,
        resource_id: str,
        memory_type: str,
        summary: str,
        content: str = "",
        category_id: str | None = None,
        importance_score: float = 0.5,
        entities: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        item_id = f"item_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """INSERT INTO memory_item
               (item_id, resource_id, category_id, memory_type, summary, content,
                importance_score, entities, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id, resource_id, category_id, memory_type, summary, content,
                importance_score, json.dumps(entities or [], ensure_ascii=False),
                json.dumps(tags or [], ensure_ascii=False), now,
            ),
        )
        self._db.commit()
        logger.debug("MemoryStoreV2: added item {}", item_id)
        return item_id

    def get_item(self, item_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT * FROM memory_item WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def query(
        self,
        memory_type: str | None = None,
        category_id: str | None = None,
        is_active: bool = True,
        limit: int = 50,
    ) -> list[dict]:
        clauses = ["is_active = ?"]
        params: list = [int(is_active)]
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if category_id:
            clauses.append("category_id = ?")
            params.append(category_id)
        sql = f"SELECT * FROM memory_item WHERE {' AND '.join(clauses)} LIMIT ?"
        params.append(limit)
        rows = self._db.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_item(self, item_id: str, **fields) -> None:
        allowed = {
            "resource_id", "category_id", "memory_type", "summary", "content",
            "importance_score", "entities", "tags", "extra",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        # Serialize list/dict fields
        for k in ("entities", "tags", "extra"):
            if k in updates and not isinstance(updates[k], str):
                updates[k] = json.dumps(updates[k], ensure_ascii=False)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [item_id]
        self._db.execute(f"UPDATE memory_item SET {set_clause} WHERE item_id = ?", params)
        self._db.commit()

    def deactivate_item(self, item_id: str) -> None:
        self._db.execute(
            "UPDATE memory_item SET is_active = 0 WHERE item_id = ?", (item_id,)
        )
        self._db.commit()
        logger.debug("MemoryStoreV2: deactivated item {}", item_id)

    def touch_item(self, item_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "UPDATE memory_item SET last_accessed_at = ?, access_count = access_count + 1 WHERE item_id = ?",
            (now, item_id),
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # Vector operations
    # ------------------------------------------------------------------

    def search_similar(
        self,
        summary: str,
        threshold: float = 0.9,
        *,
        memory_type: str | None = None,
        category_id: str | None = None,
        limit: int = 5,
    ) -> dict | None:
        """Return the most similar item if similarity >= threshold, else None.

        Pre-filtering strategy:
        1. If memory_type or category_id specified → SQLite WHERE first,
           then only compare against matching FAISS vectors.
        2. Otherwise → full FAISS search (fast for <10K items).
        """
        if self._faiss_index.ntotal == 0:
            return None

        vec = np.array([self._embedding.embed(summary)], dtype=np.float32)

        # Pre-filter: narrow candidates via SQLite
        candidate_ids: set[str] | None = None
        if memory_type or category_id:
            candidate_ids = self._query_candidate_ids(memory_type, category_id)
            if not candidate_ids:
                return None
            # Map to FAISS indices
            candidate_faiss_idxs = [
                self._id_to_idx[cid] for cid in candidate_ids
                if cid in self._id_to_idx
            ]
            if not candidate_faiss_idxs:
                return None
            # Search full index, then filter
            k = min(self._faiss_index.ntotal, max(limit * 3, 20))
            scores, indices = self._faiss_index.search(vec, k)
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0 or score < threshold:
                    continue
                item_id = self._idx_to_id.get(int(idx))
                if item_id and item_id in candidate_ids:
                    item = self.get_item(item_id)
                    if item:
                        self.touch_item(item_id)
                    return item
            return None

        # No pre-filter: full search
        scores, indices = self._faiss_index.search(vec, 1)
        if scores[0][0] < threshold:
            return None
        item_id = self._idx_to_id.get(int(indices[0][0]))
        if item_id is None:
            return None
        item = self.get_item(item_id)
        if item:
            self.touch_item(item_id)
        return item

    def search_similar_batch(
        self,
        summary: str,
        threshold: float = 0.7,
        *,
        memory_type: str | None = None,
        category_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return multiple similar items above threshold, sorted by score desc.

        Uses SQLite pre-filtering when memory_type/category_id specified.
        """
        if self._faiss_index.ntotal == 0:
            return []

        vec = np.array([self._embedding.embed(summary)], dtype=np.float32)
        k = min(self._faiss_index.ntotal, max(limit * 3, 30))

        # Pre-filter via SQLite
        candidate_ids: set[str] | None = None
        if memory_type or category_id:
            candidate_ids = self._query_candidate_ids(memory_type, category_id)
            if not candidate_ids:
                return []

        scores, indices = self._faiss_index.search(vec, k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or score < threshold:
                continue
            item_id = self._idx_to_id.get(int(idx))
            if not item_id:
                continue
            if candidate_ids and item_id not in candidate_ids:
                continue
            item = self.get_item(item_id)
            if item:
                item["_score"] = float(score)
                results.append(item)
            if len(results) >= limit:
                break
        return results

    def _query_candidate_ids(
        self, memory_type: str | None, category_id: str | None,
    ) -> set[str]:
        """SQLite pre-filter: return item_ids matching criteria."""
        clauses = ["is_active = 1"]
        params: list = []
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if category_id:
            clauses.append("category_id = ?")
            params.append(category_id)
        sql = f"SELECT item_id FROM memory_item WHERE {' AND '.join(clauses)}"
        rows = self._db.execute(sql, params).fetchall()
        return {row["item_id"] for row in rows}

    def embed_and_index(self, item_id: str, summary: str) -> None:
        vec = self._embedding.embed(summary)
        vec_np = np.array([vec], dtype=np.float32)
        faiss_idx = self._faiss_index.ntotal
        self._faiss_index.add(vec_np)

        now = datetime.now(timezone.utc).isoformat()
        summary_hash = self._embedding.content_hash(summary)
        self._db.execute(
            """INSERT OR REPLACE INTO vector_mapping
               (faiss_index, item_id, summary_hash, embedded_at, model_name)
               VALUES (?, ?, ?, ?, 'bge-small-zh-v1.5')""",
            (faiss_idx, item_id, summary_hash, now),
        )
        self._db.execute(
            "UPDATE memory_item SET embedded_at = ? WHERE item_id = ?", (now, item_id)
        )
        self._db.commit()

        self._id_to_idx[item_id] = faiss_idx
        self._idx_to_id[faiss_idx] = item_id

    def replace_vector(self, item_id: str, new_summary: str) -> None:
        """Re-embed and replace an item's FAISS vector (append-orphan strategy).

        Appends the new vector at the end, updates mapping so item_id points to the
        new slot. The old slot becomes orphaned (no item_id maps to it) and will be
        skipped by search since _idx_to_id.get() returns None. Call rebuild_faiss_index()
        periodically to compact orphaned slots.
        """
        if item_id not in self._id_to_idx:
            self.embed_and_index(item_id, new_summary)
            return

        old_faiss_idx = self._id_to_idx[item_id]
        vec = self._embedding.embed(new_summary)
        vec_np = np.array([vec], dtype=np.float32)
        new_faiss_idx = self._faiss_index.ntotal
        self._faiss_index.add(vec_np)

        now = datetime.now(timezone.utc).isoformat()
        summary_hash = self._embedding.content_hash(new_summary)
        self._db.execute("DELETE FROM vector_mapping WHERE item_id = ?", (item_id,))
        self._db.execute(
            """INSERT INTO vector_mapping
               (faiss_index, item_id, summary_hash, embedded_at, model_name)
               VALUES (?, ?, ?, ?, 'bge-small-zh-v1.5')""",
            (new_faiss_idx, item_id, summary_hash, now),
        )
        self._db.execute(
            "UPDATE memory_item SET embedded_at = ?, summary = ? WHERE item_id = ?",
            (now, new_summary, item_id),
        )
        self._db.commit()

        del self._idx_to_id[old_faiss_idx]
        self._id_to_idx[item_id] = new_faiss_idx
        self._idx_to_id[new_faiss_idx] = item_id
        logger.debug(
            "MemoryStoreV2: replaced vector for {} (slot {} -> {})",
            item_id, old_faiss_idx, new_faiss_idx,
        )

    def compute_category_vector_from_items(
        self,
        category_id: str,
        recency_weight: float = 0.4,
        importance_weight: float = 0.6,
    ) -> list[float] | None:
        """Compute a weighted-average vector for a category from its member items.

        Weight = importance_weight * importance_score + recency_weight * freshness.
        Uses FAISS reconstruct() to retrieve stored vectors.
        Returns L2-normalized vector, or None if no embedded items.
        """
        rows = self._db.execute(
            """SELECT item_id, importance_score, last_accessed_at, created_at
               FROM memory_item
               WHERE category_id = ? AND is_active = 1 AND embedded_at IS NOT NULL""",
            (category_id,),
        ).fetchall()
        if not rows:
            return None

        now = datetime.now(timezone.utc)
        vectors: list = []
        weights: list[float] = []

        for row in rows:
            item_id = row["item_id"]
            if item_id not in self._id_to_idx:
                continue
            faiss_idx = self._id_to_idx[item_id]
            if faiss_idx >= self._faiss_index.ntotal:
                continue  # stale mapping, skip
            vec = self._faiss_index.reconstruct(faiss_idx)
            vectors.append(vec)

            importance = row["importance_score"] or 0.5
            ts_str = row["last_accessed_at"] or row["created_at"]
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                freshness = max(0.0, 1.0 - (now - ts).total_seconds() / 86400 / 30)
            except (ValueError, TypeError):
                freshness = 0.3
            weights.append(importance_weight * importance + recency_weight * freshness)

        if not vectors:
            return None

        w = np.array(weights, dtype=np.float32)
        w /= w.sum()
        avg = np.average(np.array(vectors, dtype=np.float32), axis=0, weights=w)
        norm = np.linalg.norm(avg)
        if norm > 1e-10:
            avg = avg / norm
        return avg.tolist()

    def rebuild_faiss_index(self, index_type: str = "Flat") -> None:
        """Rebuild FAISS index from all memory_item rows."""
        rows = self._db.execute(
            "SELECT item_id, summary FROM memory_item WHERE is_active = 1 AND embedded_at IS NOT NULL"
        ).fetchall()
        if not rows:
            logger.info("MemoryStoreV2: no embedded items, creating empty index")
            if index_type == "IVF1024,Flat":
                self._faiss_index = faiss.IndexIVFFlat(
                    faiss.IndexFlatIP(self._dimension), self._dimension, 1024
                )
            else:
                self._faiss_index = faiss.IndexFlatIP(self._dimension)
            self._save_faiss()
            return

        summaries = [r["summary"] for r in rows]
        item_ids = [r["item_id"] for r in rows]
        vecs = np.array(self._embedding.embed_batch(summaries), dtype=np.float32)

        if index_type == "IVF1024,Flat":
            quantizer = faiss.IndexFlatIP(self._dimension)
            index = faiss.IndexIVFFlat(quantizer, self._dimension, min(1024, len(rows)))
            index.train(vecs)
            index.add(vecs)
        else:
            index = faiss.IndexFlatIP(self._dimension)
            index.add(vecs)

        self._faiss_index = index

        # Rebuild mapping
        self._db.execute("DELETE FROM vector_mapping")
        now = datetime.now(timezone.utc).isoformat()
        self._id_to_idx.clear()
        self._idx_to_id.clear()
        for i, (item_id, summary) in enumerate(zip(item_ids, summaries)):
            summary_hash = self._embedding.content_hash(summary)
            self._db.execute(
                "INSERT INTO vector_mapping (faiss_index, item_id, summary_hash, embedded_at, model_name) VALUES (?, ?, ?, ?, 'bge-small-zh-v1.5')",
                (i, item_id, summary_hash, now),
            )
            self._id_to_idx[item_id] = i
            self._idx_to_id[i] = item_id
        self._db.commit()
        self._save_faiss()
        logger.info("MemoryStoreV2: rebuilt FAISS index ({}) with {} items", index_type, len(rows))

    def get_unembedded_items(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM memory_item WHERE is_active = 1 AND embedded_at IS NULL"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_all_active_items(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM memory_item WHERE is_active = 1"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Ranked search + async metadata
    # ------------------------------------------------------------------

    def search_with_ranking(
        self,
        query_vec: list[float],
        category_ids: list[str] | None = None,
        top_k: int = 5,
        threshold: float = 0.6,
    ) -> list[dict]:
        """Layered retrieval with weighted re-ranking.

        Pipeline:
        1. Category subset filter (via SQLite WHERE) — shrinks search space 90%+
        2. FAISS vector search on filtered candidates
        3. Weighted re-ranking: similarity*0.5 + importance*0.2 + access*0.15 + freshness*0.15

        Args:
            query_vec: Pre-computed query embedding (512-dim).
            category_ids: Restrict search to these categories. None = search all.
            top_k: Max results to return.
            threshold: Minimum cosine similarity threshold.

        Returns:
            List of item dicts with `_final_score` and `_score` fields added.
        """
        if self._faiss_index.ntotal == 0:
            return []

        # Step 1: SQLite pre-filter for category subset
        candidate_ids: set[str] | None = None
        if category_ids:
            all_candidates = set()
            for cid in category_ids:
                ids = self._query_candidate_ids(memory_type=None, category_id=cid)
                all_candidates.update(ids)
            candidate_ids = all_candidates
            if not candidate_ids:
                return []

        # Step 2: FAISS search
        vec = np.array([query_vec], dtype=np.float32)
        k = min(self._faiss_index.ntotal, max(top_k * 4, 30))
        scores, indices = self._faiss_index.search(vec, k)

        # Step 3: Collect candidates + re-rank
        now = datetime.now(timezone.utc)
        candidates = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or score < threshold:
                continue
            item_id = self._idx_to_id.get(int(idx))
            if not item_id:
                continue
            if candidate_ids and item_id not in candidate_ids:
                continue
            item = self.get_item(item_id)
            if item:
                item["_score"] = float(score)
                candidates.append(item)
            if len(candidates) >= top_k * 2:
                break

        # Weighted re-ranking
        for item in candidates:
            importance = item.get("importance_score", 0.5) or 0.5
            access_count = item.get("access_count", 0) or 0
            access_score = min(access_count / 100, 1.0)

            # Freshness: decay over 30 days
            last_accessed = item.get("last_accessed_at")
            if last_accessed:
                try:
                    last_dt = datetime.fromisoformat(last_accessed)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    days_since = (now - last_dt).total_seconds() / 86400
                    freshness = max(0, 1.0 - days_since / 30)
                except (ValueError, TypeError):
                    freshness = 0.5
            else:
                freshness = 0.3  # Never accessed — low freshness

            item["_final_score"] = (
                item["_score"] * 0.5 +
                importance * 0.2 +
                access_score * 0.15 +
                freshness * 0.15
            )

        candidates.sort(key=lambda x: x["_final_score"], reverse=True)
        return candidates[:top_k]

    async def touch_items_async(self, item_ids: list[str]) -> None:
        """Batch update last_accessed_at + access_count (call from async context)."""
        if not item_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" for _ in item_ids)
        self._db.execute(
            f"UPDATE memory_item SET last_accessed_at = ?, access_count = access_count + 1 "
            f"WHERE item_id IN ({placeholders})",
            [now] + item_ids,
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def _save_faiss(self) -> None:
        faiss.write_index(self._faiss_index, str(self._index_path))

    def close(self) -> None:
        self._save_faiss()
        self._db.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for field in ("entities", "tags", "extra"):
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
