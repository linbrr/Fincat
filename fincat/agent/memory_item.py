"""MemoryItem — atomic memory units with decay, frequency, and confidence tracking.

MemoryItems are the data layer (structured, queryable, monitorable).
Category Markdown files are the expression layer (human-readable, injectable).
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from fincat.agent.conflict_detector import ConflictDetector, ConflictResult
    from fincat.agent.memory_store_v2 import MemoryStoreV2

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HALF_LIFE_DAYS = 30  # decay half-life
DECAY_LAMBDA = math.log(2) / HALF_LIFE_DAYS

CATEGORIES = ["preference", "knowledge", "case", "compliance", "profile", "insight", "behavior", "event", "goal"]

# Confidence ramp
CONFIDENCE_TABLE = {1: 0.5, 2: 0.6, 3: 0.7, 5: 0.9}


@dataclass
class MemoryItem:
    """Atomic, self-contained memory unit with temporal metadata."""

    item_id: str
    timestamp: datetime
    content: str
    category: str
    source_session: str
    source_round: int = 0

    # Frequency & decay
    frequency: int = 0
    last_accessed: datetime | None = None
    decay_score: float = 1.0

    # Confidence grows with repeated observations
    confidence: float = 0.5

    # Conflict detection fields
    source_type: str = "user"  # user | system | third_party
    conflict_with: str | None = None  # 冲突记忆的 ID
    is_archived: bool = False  # 是否已归档到冷存储

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "timestamp": self.timestamp.isoformat(),
            "content": self.content,
            "category": self.category,
            "source_session": self.source_session,
            "source_round": self.source_round,
            "frequency": self.frequency,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
            "decay_score": round(self.decay_score, 4),
            "confidence": round(self.confidence, 2),
            "source_type": self.source_type,
            "conflict_with": self.conflict_with,
            "is_archived": self.is_archived,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryItem:
        la = d.get("last_accessed")
        return cls(
            item_id=d["item_id"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            content=d["content"],
            category=d.get("category", "knowledge"),
            source_session=d.get("source_session", ""),
            source_round=d.get("source_round", 0),
            frequency=d.get("frequency", 0),
            last_accessed=datetime.fromisoformat(la) if la else None,
            decay_score=d.get("decay_score", 1.0),
            confidence=d.get("confidence", 0.5),
            source_type=d.get("source_type", "user"),
            conflict_with=d.get("conflict_with"),
            is_archived=d.get("is_archived", False),
        )

    # ------------------------------------------------------------------
    # Decay
    # ------------------------------------------------------------------

    def compute_decay(self, *, now: datetime | None = None) -> float:
        """Recompute decay_score based on days since last access."""
        now = now or datetime.now(timezone.utc)
        ref = self.last_accessed or self.timestamp
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        days = (now - ref).total_seconds() / 86400.0
        self.decay_score = self.confidence * math.exp(-DECAY_LAMBDA * max(days, 0))
        return self.decay_score

    def touch(self, *, now: datetime | None = None) -> None:
        """Record an access, bump frequency, and recompute decay."""
        self.last_accessed = now or datetime.now(timezone.utc)
        self.frequency += 1
        self.compute_decay(now=now)

    @staticmethod
    def confidence_for_observations(n: int) -> float:
        """Map observation count to confidence score."""
        thresholds = sorted(CONFIDENCE_TABLE.keys(), reverse=True)
        for t in thresholds:
            if n >= t:
                return CONFIDENCE_TABLE[t]
        return 0.5


# ---------------------------------------------------------------------------
# MemoryItemStore — append-only JSONL + query
# ---------------------------------------------------------------------------


class MemoryItemStore:
    """Append-only JSONL store for MemoryItems with dedup merging."""

    _SIMILARITY_THRESHOLD = 0.8

    def __init__(self, path: Path):
        self.path = Path(path)
        self._items: dict[str, MemoryItem] = {}
        self._load()

    # -- I/O ---------------------------------------------------------------

    def _load(self) -> None:
        """Load all items from the JSONL file."""
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = MemoryItem.from_dict(json.loads(line))
                        self._items[item.item_id] = item
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            logger.warning("Failed to load MemoryItem store from {}", self.path)

    def _append(self, item: MemoryItem) -> None:
        """Append a single item to the JSONL file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        self._items[item.item_id] = item

    def _rewrite(self) -> None:
        """Rewrite the entire JSONL file from in-memory items."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        items = sorted(self._items.values(), key=lambda it: it.timestamp)
        for item in items:
            item.compute_decay()  # ensure decay_score is fresh before writing
        with open(self.path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")

    # -- CRUD --------------------------------------------------------------

    def add(
        self,
        content: str,
        category: str,
        *,
        source_session: str = "",
        source_round: int = 0,
        timestamp: datetime | None = None,
    ) -> MemoryItem:
        """Add a new MemoryItem, merging if a similar one exists.

        Returns the new or merged item.
        """
        ts = timestamp or datetime.now(timezone.utc)
        existing = self._find_similar(content, category)
        if existing:
            existing.frequency += 1
            existing.confidence = MemoryItem.confidence_for_observations(existing.frequency)
            existing.last_accessed = ts
            existing.compute_decay(now=ts)
            self._rewrite()
            logger.debug(
                "MemoryItem merged: id={} freq={} conf={:.2f}",
                existing.item_id, existing.frequency, existing.confidence,
            )
            return existing

        item = MemoryItem(
            item_id=str(uuid.uuid4())[:8],
            timestamp=ts,
            content=content,
            category=category,
            source_session=source_session,
            source_round=source_round,
            frequency=1,
            last_accessed=ts,
            decay_score=0.5,  # initial confidence 0.5
            confidence=0.5,
        )
        self._append(item)
        return item

    async def add_with_conflict_check(
        self,
        content: str,
        category: str,
        *,
        conflict_detector: ConflictDetector,
        store_v2: MemoryStoreV2,
        source_type: str = "user",
        source_session: str = "",
        source_round: int = 0,
        timestamp: datetime | None = None,
    ) -> MemoryItem | list[MemoryItem]:
        """Add memory with conflict detection — new overwrites old if conflicting.

        Args:
            content: 记忆内容
            category: 分类
            conflict_detector: 冲突检测器
            store_v2: 向量存储（用于语义搜索)
            source_type: 来源类型 (user/system/third_party)
            source_session: 来源会话
            source_round: 来源轮次
            timestamp: 时间戳

        Returns:
            单个 MemoryItem（正常存入或覆盖）或列表（保留双版本时返回两个）
        """
        from fincat.agent.conflict_detector import ConflictAction

        ts = timestamp or datetime.now(timezone.utc)

        # Step 1: L1 文本去重 (阈值 0.8)
        existing_text = self._find_similar(content, category)
        if existing_text:
            # 原有合并逻辑
            existing_text.frequency += 1
            existing_text.confidence = MemoryItem.confidence_for_observations(existing_text.frequency)
            existing_text.last_accessed = ts
            existing_text.compute_decay(now=ts)
            self._rewrite()
            logger.debug(
                "MemoryItem merged (text dedup): id={} freq={} conf={:.2f}",
                existing_text.item_id, existing_text.frequency, existing_text.confidence,
            )
            return existing_text

        # Step 2: L2 向量相似度搜索 (阈值 0.95)
        summary = content[:200]
        similar_vec = store_v2.search_similar(summary, threshold=0.95)

        if similar_vec:
            existing_id = similar_vec["item_id"]
            existing_item = self.get(existing_id)

            if existing_item:
                # Step 3: LLM 冲突检测
                result: ConflictResult = await conflict_detector.detect_and_resolve(
                    new_content=content,
                    new_timestamp=ts,
                    new_source_type=source_type,
                    new_confidence=0.5,
                    new_frequency=1,
                    existing_content=existing_item.content,
                    existing_id=existing_id,
                    existing_timestamp=existing_item.timestamp,
                    existing_source_type=existing_item.source_type,
                    existing_confidence=existing_item.confidence,
                    existing_frequency=existing_item.frequency,
                )

                if result.is_conflict:
                    if result.action == ConflictAction.OVERWRITE:
                        # 覆盖：胜者覆盖败者
                        if result.winner_id == "new":
                            # 新记忆胜出 → 覆盖旧记忆
                            return self._overwrite_memory(
                                existing_item, content, ts, source_type, source_session, source_round,
                            )
                        else:
                            # 旧记忆胜出 → 保留旧的，忽略新的
                            existing_item.touch(now=ts)
                            logger.info("旧记忆优先级更高，保留: id={}", existing_id)
                            return existing_item

                    elif result.action == ConflictAction.KEEP_BOTH:
                        # 保留双版本 + 标记冲突
                        return self._keep_both(
                            content, category, existing_id,
                            ts, source_type, source_session, source_round,
                        )

                # 不矛盾 → 原有合并逻辑
                existing_item.frequency += 1
                existing_item.confidence = MemoryItem.confidence_for_observations(existing_item.frequency)
                existing_item.last_accessed = ts
                existing_item.compute_decay(now=ts)
                self._rewrite()
                logger.debug(
                    "MemoryItem merged (no conflict): id={} freq={}",
                    existing_item.item_id, existing_item.frequency,
                )
                return existing_item

        # 无相似 → 新建
        return self._create_new(content, category, ts, source_type, source_session, source_round)

    def _create_new(
        self,
        content: str,
        category: str,
        ts: datetime,
        source_type: str,
        source_session: str,
        source_round: int,
    ) -> MemoryItem:
        """创建新记忆"""
        item = MemoryItem(
            item_id=str(uuid.uuid4())[:8],
            timestamp=ts,
            content=content,
            category=category,
            source_session=source_session,
            source_round=source_round,
            frequency=1,
            last_accessed=ts,
            decay_score=0.5,
            confidence=0.5,
            source_type=source_type,
        )
        self._append(item)
        logger.debug("MemoryItem created: id={}", item.item_id)
        return item

    def _overwrite_memory(
        self,
        existing: MemoryItem,
        new_content: str,
        ts: datetime,
        source_type: str,
        source_session: str,
        source_round: int,
    ) -> MemoryItem:
        """用新记忆覆盖旧记忆"""
        old_id = existing.item_id
        existing.content = new_content
        existing.timestamp = ts
        existing.source_type = source_type
        existing.source_session = source_session
        existing.source_round = source_round
        existing.confidence = 0.5  # 重置置信度
        existing.frequency = 1
        existing.last_accessed = ts
        existing.conflict_with = None  # 清除冲突标记
        existing.compute_decay(now=ts)
        self._rewrite()
        logger.info("MemoryItem overwritten: id={}", old_id)
        return existing

    def _keep_both(
        self,
        content: str,
        category: str,
        existing_id: str,
        ts: datetime,
        source_type: str,
        source_session: str,
        source_round: int,
    ) -> list[MemoryItem]:
        """保留双版本，标记冲突"""
        # 创建新记忆，标记与旧记忆冲突
        new_item = MemoryItem(
            item_id=str(uuid.uuid4())[:8],
            timestamp=ts,
            content=content,
            category=category,
            source_session=source_session,
            source_round=source_round,
            frequency=1,
            last_accessed=ts,
            decay_score=0.5,
            confidence=0.5,
            source_type=source_type,
            conflict_with=existing_id,
        )
        self._append(new_item)

        # 标记旧记忆也与新记忆冲突
        existing = self._items.get(existing_id)
        if existing:
            existing.conflict_with = new_item.item_id
            self._rewrite()

        logger.info(
            "MemoryItem conflict kept both: new={} existing={}",
            new_item.item_id, existing_id,
        )
        return [x for x in [new_item, existing] if x is not None]

    def get(self, item_id: str) -> MemoryItem | None:
        item = self._items.get(item_id)
        if item:
            item.touch()
        return item

    def query(
        self,
        *,
        category: str | None = None,
        min_decay: float | None = None,
        min_frequency: int = 0,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """Query items with optional filters, sorted by decay_score descending."""
        results = []
        now = datetime.now(timezone.utc)
        for item in self._items.values():
            if category and item.category != category:
                continue
            item.compute_decay(now=now)
            if min_decay is not None and item.decay_score < min_decay:
                continue
            if item.frequency < min_frequency:
                continue
            if since and item.timestamp < since:
                continue
            results.append(item)
        results.sort(key=lambda it: it.decay_score, reverse=True)
        return results[:limit]

    def all(self) -> list[MemoryItem]:
        return list(self._items.values())

    def get_recent(self, days: int = 7) -> list[MemoryItem]:
        """Return non-archived items created/updated within the last N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results = []
        for item in self._items.values():
            if item.is_archived:
                continue
            ts = item.last_accessed or item.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                results.append(item)
        results.sort(key=lambda it: it.timestamp, reverse=True)
        return results

    def remove(self, item_id: str) -> bool:
        if item_id in self._items:
            del self._items[item_id]
            self._rewrite()
            return True
        return False

    def archive(self, item_id: str) -> bool:
        """Move a low-decay item to archive.jsonl and remove from active store."""
        item = self._items.get(item_id)
        if not item:
            return False
        archive_path = self.path.parent / "archive.jsonl"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(archive_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Failed to archive item {}", item_id)
            return False
        del self._items[item_id]
        self._rewrite()
        logger.info("Archived MemoryItem: id={} decay={:.2f}", item_id, item.decay_score)
        return True

    # -- dedup -------------------------------------------------------------

    def _find_similar(self, content: str, category: str) -> MemoryItem | None:
        """Find an existing item in the same category with high content similarity."""
        best_score = 0.0
        best_item: MemoryItem | None = None
        for item in self._items.values():
            if item.category != category:
                continue
            score = self._token_similarity(content, item.content)
            if score > best_score:
                best_score = score
                best_item = item
        return best_item if best_score >= self._SIMILARITY_THRESHOLD else None

    @staticmethod
    def _token_similarity(a: str, b: str) -> float:
        """Simple token-overlap similarity for Chinese + English text."""
        def _tokens(s: str) -> set[str]:
            # Split on whitespace and also extract 2-char Chinese bigrams
            parts = set(s.split())
            # Add character bigrams for CJK text
            chars = "".join(ch for ch in s if "一" <= ch <= "鿿")
            for i in range(len(chars) - 1):
                parts.add(chars[i : i + 2])
            return parts

        ta = _tokens(a)
        tb = _tokens(b)
        if not ta or not tb:
            return 0.0
        intersection = ta & tb
        return len(intersection) / min(len(ta), len(tb))

    def __len__(self) -> int:
        return len(self._items)


# ---------------------------------------------------------------------------
# Factory — create store bound to memory directory
# ---------------------------------------------------------------------------


def create_item_store(memory_dir: Path) -> MemoryItemStore:
    return MemoryItemStore(memory_dir / "items.jsonl")
