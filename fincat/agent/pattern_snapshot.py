"""Pattern snapshot — captures MemoryItem distribution at a point in time.

Used by the PATTERN trigger to detect behavior shifts by comparing
the current item distribution against a historical baseline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

# Entity extraction patterns (same as entity_extractor.py / prefilter)
_INSTITUTION_RE = re.compile(
    r"(中国银行|工商银行|建设银行|农业银行|交通银行|招商银行|"
    r"中信银行|浦发银行|民生银行|兴业银行|光大银行|华夏银行|"
    r"邮储银行|平安银行|北京银行|上海银行|南京银行|宁波银行|"
    r"证监会|银保监会|央行|人民银行|财政部|税务总局)"
)
_PRODUCT_RE = re.compile(
    r"((?:个人|企业|公积金|商业|组合|经营性|消费)?"
    r"(?:贷款|理财|基金|保险|存款|信用卡|外汇|期货|期权|ETF|国债|信托)"
    r"(?:产品|计划|项目|方案)?)"
)
_STOCK_CODE_RE = re.compile(r"[036]\d{5}")
_METRIC_RE = re.compile(r"PE|PB|ROE|ROA|EPS|净利润|营收|毛利率|净利率|市盈率|市净率")


@dataclass
class PatternSnapshot:
    """A point-in-time snapshot of MemoryItem distribution."""

    timestamp: datetime
    category_counts: dict[str, int] = field(default_factory=dict)
    category_frequencies: dict[str, int] = field(default_factory=dict)
    top_entities: list[str] = field(default_factory=list)
    entity_category_map: dict[str, str] = field(default_factory=dict)
    trend_analysis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "category_counts": self.category_counts,
            "category_frequencies": self.category_frequencies,
            "top_entities": self.top_entities,
            "entity_category_map": self.entity_category_map,
            "trend_analysis": self.trend_analysis,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PatternSnapshot:
        return cls(
            timestamp=datetime.fromisoformat(d["timestamp"]),
            category_counts=d.get("category_counts", {}),
            category_frequencies=d.get("category_frequencies", {}),
            top_entities=d.get("top_entities", []),
            entity_category_map=d.get("entity_category_map", {}),
            trend_analysis=d.get("trend_analysis"),
        )


def extract_entities_from_text(text: str) -> list[str]:
    """Extract named entities from text using regex patterns."""
    entities: list[str] = []
    for pattern in [_INSTITUTION_RE, _PRODUCT_RE]:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            if name and name not in entities:
                entities.append(name)
    for m in _STOCK_CODE_RE.finditer(text):
        code = m.group()
        if code not in entities:
            entities.append(code)
    for m in _METRIC_RE.finditer(text):
        metric = m.group()
        if metric not in entities:
            entities.append(metric)
    return entities


def build_snapshot(items: list[Any], top_n: int = 20) -> PatternSnapshot:
    """Build a PatternSnapshot from a list of MemoryItem-like objects.

    Args:
        items: Objects with .category, .frequency, .content attributes.
        top_n: Number of top entities to keep.
    """
    category_counts: dict[str, int] = {}
    category_frequencies: dict[str, int] = {}
    entity_freq: dict[str, int] = {}
    entity_category_map: dict[str, str] = {}

    for item in items:
        cat = item.category
        category_counts[cat] = category_counts.get(cat, 0) + 1
        category_frequencies[cat] = category_frequencies.get(cat, 0) + getattr(item, "frequency", 1)

        for entity in extract_entities_from_text(item.content):
            entity_freq[entity] = entity_freq.get(entity, 0) + getattr(item, "frequency", 1)
            if entity not in entity_category_map:
                entity_category_map[entity] = cat

    # Sort entities by frequency, keep top N
    sorted_entities = sorted(entity_freq.items(), key=lambda x: x[1], reverse=True)
    top_entities = [name for name, _ in sorted_entities[:top_n]]

    return PatternSnapshot(
        timestamp=datetime.now(timezone.utc),
        category_counts=category_counts,
        category_frequencies=category_frequencies,
        top_entities=top_entities,
        entity_category_map=entity_category_map,
    )


# ---------------------------------------------------------------------------
# Snapshot Store — append-only JSONL, keeps last N
# ---------------------------------------------------------------------------


class PatternSnapshotStore:
    """Append-only JSONL store for PatternSnapshots, keeps last N entries."""

    _DEFAULT_PATH = "memory/.pattern_snapshots.jsonl"

    def __init__(self, path: Path | None = None, keep_last: int = 5):
        self._path = path or Path(self._DEFAULT_PATH)
        self._keep_last = keep_last

    def save(self, snapshot: PatternSnapshot) -> None:
        """Append a snapshot and trim to keep_last entries."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")
        self._trim()
        logger.debug("PatternSnapshot saved ({} entities)", len(snapshot.top_entities))

    def load_latest(self) -> PatternSnapshot | None:
        """Load the most recent snapshot."""
        history = self.load_history(limit=1)
        return history[0] if history else None

    def load_history(self, limit: int = 5) -> list[PatternSnapshot]:
        """Load the most recent N snapshots (newest first)."""
        if not self._path.exists():
            return []
        snapshots: list[PatternSnapshot] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        snapshots.append(PatternSnapshot.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            return []
        # Return newest first
        snapshots.sort(key=lambda s: s.timestamp, reverse=True)
        return snapshots[:limit]

    def _trim(self) -> None:
        """Keep only the last N entries in the file."""
        all_snapshots: list[PatternSnapshot] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        all_snapshots.append(PatternSnapshot.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            return
        if len(all_snapshots) <= self._keep_last:
            return
        all_snapshots.sort(key=lambda s: s.timestamp)
        with open(self._path, "w", encoding="utf-8") as f:
            for s in all_snapshots[-self._keep_last:]:
                f.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
