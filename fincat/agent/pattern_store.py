"""PatternStore — patterns 表管理，支持向量去重和 occurrence_count 累计。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from fincat.agent.embedding import EmbeddingEngine

# occurrence_count 达到此阈值时升级为 confirmed
CONFIRMED_THRESHOLD = 3


class PatternStore:
    """管理 patterns 表：存储、去重、升级、查询。"""

    def __init__(self, db_path: Path, embedding: EmbeddingEngine):
        self._db_path = db_path
        self._embedding = embedding
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

        # In-memory vector cache: pattern_id → embedding vector
        self._vec_cache: dict[str, list[float]] = {}
        self._load_vec_cache()

    def _init_schema(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS patterns (
                pattern_id    TEXT PRIMARY KEY,
                pattern_type  TEXT NOT NULL,
                description   TEXT NOT NULL,
                evidence_ids  TEXT DEFAULT '[]',
                occurrence_count INTEGER DEFAULT 1,
                confidence    REAL DEFAULT 0.5,
                status        TEXT DEFAULT 'observed',
                periodicity   TEXT,
                first_seen    TEXT NOT NULL,
                last_seen     TEXT NOT NULL,
                extra         TEXT DEFAULT '{}'
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS pattern_vectors (
                pattern_id    TEXT PRIMARY KEY,
                vector        TEXT NOT NULL,
                embedded_at   TEXT NOT NULL
            )
        """)
        self._db.commit()
        # Migrate old schema: drop legacy faiss_index column if present
        try:
            cols = [r[1] for r in self._db.execute("PRAGMA table_info(pattern_vectors)").fetchall()]
            if "faiss_index" in cols and "vector" not in cols:
                self._db.execute("DROP TABLE pattern_vectors")
                self._db.execute("""
                    CREATE TABLE pattern_vectors (
                        pattern_id    TEXT PRIMARY KEY,
                        vector        TEXT NOT NULL,
                        embedded_at   TEXT NOT NULL
                    )
                """)
                self._db.commit()
                logger.info("PatternStore: migrated pattern_vectors schema")
        except Exception:
            pass

    def _load_vec_cache(self) -> None:
        rows = self._db.execute(
            "SELECT pattern_id, vector FROM pattern_vectors"
        ).fetchall()
        for row in rows:
            try:
                self._vec_cache[row["pattern_id"]] = json.loads(row["vector"])
            except (json.JSONDecodeError, TypeError):
                continue

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_or_merge(
        self,
        pattern_type: str,
        description: str,
        evidence_ids: list[str],
        confidence: float = 0.5,
        periodicity: str | None = None,
        occurrence_count: int = 1,
        extra: dict | None = None,
    ) -> tuple[str, bool]:
        """添加或合并 pattern。返回 (pattern_id, is_new)。

        向量去重逻辑：
        - 相似度 ≥0.85 → 合并到已有 pattern（occurrence_count++, evidence_ids 合并）
        - 相似度 <0.85 → 新增 pattern
        """
        # 向量搜索已有 patterns
        similar = self._search_similar(description, threshold=0.85)

        if similar:
            # 合并到已有 pattern
            pid = similar["pattern_id"]
            self._merge_pattern(
                pid,
                new_evidence=evidence_ids,
                new_confidence=confidence,
                new_occurrence_count=occurrence_count,
            )
            logger.debug("PatternStore: merged into existing {}", pid)
            return pid, False

        # 新增 pattern
        pid = f"pat_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """INSERT INTO patterns
               (pattern_id, pattern_type, description, evidence_ids,
                occurrence_count, confidence, status, periodicity,
                first_seen, last_seen, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid, pattern_type, description,
                json.dumps(evidence_ids, ensure_ascii=False),
                occurrence_count, confidence, "observed", periodicity,
                now, now,
                json.dumps(extra or {}, ensure_ascii=False),
            ),
        )
        self._db.commit()

        # 向量索引
        self._embed_and_index(pid, description)

        logger.debug("PatternStore: added new pattern {} (type={})", pid, pattern_type)
        return pid, True

    def get_confirmed(self, pattern_type: str | None = None) -> list[dict]:
        """获取所有 confirmed patterns，用于规则生成。"""
        if pattern_type:
            rows = self._db.execute(
                "SELECT * FROM patterns WHERE status = 'confirmed' AND pattern_type = ?",
                (pattern_type,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM patterns WHERE status = 'confirmed'"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_all(self, status: str | None = None) -> list[dict]:
        """获取所有 patterns。"""
        if status:
            rows = self._db.execute(
                "SELECT * FROM patterns WHERE status = ?", (status,)
            ).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM patterns").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_status(self, pattern_id: str, status: str) -> None:
        """更新 pattern 状态。"""
        self._db.execute(
            "UPDATE patterns SET status = ? WHERE pattern_id = ?",
            (status, pattern_id),
        )
        self._db.commit()

    def delete_expired(self, days: int = 30) -> int:
        """删除超过指定天数的 expired patterns。"""
        rows = self._db.execute(
            "SELECT pattern_id FROM patterns WHERE status = 'expired'"
        ).fetchall()
        count = len(rows)
        if count:
            pids = [r["pattern_id"] for r in rows]
            placeholders = ",".join("?" * len(pids))
            self._db.execute(
                f"DELETE FROM patterns WHERE pattern_id IN ({placeholders})", pids
            )
            self._db.execute(
                f"DELETE FROM pattern_vectors WHERE pattern_id IN ({placeholders})", pids
            )
            self._db.commit()
            for pid in pids:
                self._vec_cache.pop(pid, None)
            logger.info("PatternStore: deleted {} expired patterns", count)
        return count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _merge_pattern(
        self,
        pattern_id: str,
        new_evidence: list[str],
        new_confidence: float,
        new_occurrence_count: int,
    ) -> None:
        """合并新数据到已有 pattern。"""
        row = self._db.execute(
            "SELECT * FROM patterns WHERE pattern_id = ?", (pattern_id,)
        ).fetchone()
        if not row:
            return

        existing_evidence = json.loads(row["evidence_ids"] or "[]")
        merged_evidence = list(set(existing_evidence + new_evidence))
        merged_count = row["occurrence_count"] + new_occurrence_count
        # 置信度取加权平均
        merged_confidence = (
            row["confidence"] * row["occurrence_count"] + new_confidence * new_occurrence_count
        ) / merged_count

        now = datetime.now(timezone.utc).isoformat()
        new_status = "confirmed" if merged_count >= CONFIRMED_THRESHOLD else row["status"]

        self._db.execute(
            """UPDATE patterns SET
               evidence_ids = ?, occurrence_count = ?, confidence = ?,
               status = ?, last_seen = ?
               WHERE pattern_id = ?""",
            (
                json.dumps(merged_evidence, ensure_ascii=False),
                merged_count,
                min(merged_confidence, 1.0),
                new_status,
                now,
                pattern_id,
            ),
        )
        self._db.commit()

        if new_status == "confirmed" and row["status"] != "confirmed":
            logger.info(
                "PatternStore: pattern {} upgraded to confirmed (count={})",
                pattern_id, merged_count,
            )

    def _search_similar(self, description: str, threshold: float = 0.85) -> dict | None:
        """向量搜索相似 pattern（使用内存缓存的向量，不重新 embed）。"""
        if not self._vec_cache:
            return None

        try:
            vec = self._embedding.embed(description)
        except Exception:
            return None

        best_score = 0.0
        best_pid = None
        for pid, cached_vec in self._vec_cache.items():
            try:
                score = self._embedding.cosine_similarity(vec, cached_vec)
                if score > best_score:
                    best_score = score
                    best_pid = pid
            except Exception:
                continue

        if best_score >= threshold and best_pid:
            row = self._db.execute(
                "SELECT * FROM patterns WHERE pattern_id = ?", (best_pid,)
            ).fetchone()
            if row:
                result = self._row_to_dict(row)
                result["_score"] = best_score
                return result
        return None

    def _embed_and_index(self, pattern_id: str, description: str) -> None:
        """将 pattern description 向量化并缓存到内存 + SQLite。"""
        try:
            vec = self._embedding.embed(description)
            now = datetime.now(timezone.utc).isoformat()
            self._db.execute(
                """INSERT OR REPLACE INTO pattern_vectors
                   (pattern_id, vector, embedded_at)
                   VALUES (?, ?, ?)""",
                (pattern_id, json.dumps(vec), now),
            )
            self._db.commit()
            self._vec_cache[pattern_id] = vec
        except Exception as e:
            logger.warning("PatternStore: embedding failed for {}: {}", pattern_id, e)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["evidence_ids"] = json.loads(d.get("evidence_ids") or "[]")
        d["extra"] = json.loads(d.get("extra") or "{}")
        return d
