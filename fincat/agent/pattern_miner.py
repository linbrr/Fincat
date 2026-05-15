"""PatternMiner — deep pattern mining: time periodicity, semantic association, entity association."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from fincat.agent.resource_store import ResourceStore

# Confidence thresholds
_TIME_PERIODICITY_THRESHOLD = 0.8
_SEMANTIC_ASSOCIATION_THRESHOLD = 0.7
_ENTITY_ASSOCIATION_THRESHOLD = 0.6


class PatternMiner:
    """Deep pattern mining: time periodicity + semantic association + entity association."""

    def __init__(self, resource_store: ResourceStore, memory_db: Path, embedding=None):
        self._resource_store = resource_store
        self._memory_db = memory_db
        self._embedding = embedding
        self._known_entities: set[str] = set()
        self._known_tags: set[str] = set()
        self._load_known_entities()

    def _load_known_entities(self) -> None:
        """Load entities and tags from memory_item table for topic extraction."""
        if not self._memory_db.exists():
            return
        try:
            conn = sqlite3.connect(str(self._memory_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT entities, tags FROM memory_item WHERE is_active = 1"
            ).fetchall()
            conn.close()
            for row in rows:
                try:
                    entities = json.loads(row["entities"] or "[]")
                    tags = json.loads(row["tags"] or "[]")
                    self._known_entities.update(e for e in entities if len(e) >= 2)
                    self._known_tags.update(t for t in tags if len(t) >= 2)
                except (json.JSONDecodeError, TypeError):
                    continue
        except sqlite3.Error:
            pass

    async def mine_time_periodicity(self, days: int = 30) -> list[dict]:
        """Mine time-based periodic patterns from interaction logs.

        Analyzes:
        - Day-of-week patterns (e.g., "every Friday afternoon")
        - Hour-of-day patterns (e.g., "every morning at 9am")
        """
        logs = self._resource_store.read_recent_logs(hours=days * 24)
        if not logs:
            return []

        # Group by (weekday, hour) → actions
        time_actions: dict[tuple[int, int, str], int] = defaultdict(int)
        action_totals: Counter[str] = Counter()

        for log in logs:
            ts = log.get("timestamp", "")
            action = log.get("action", "")
            if not ts or not action:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            key = (dt.weekday(), dt.hour, action)
            time_actions[key] += 1
            action_totals[action] += 1

        patterns = []
        for (weekday, hour, action), count in time_actions.items():
            total = action_totals[action]
            if total < 3:
                continue
            confidence = count / total
            if confidence >= _TIME_PERIODICITY_THRESHOLD:
                day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                patterns.append({
                    "type": "time_periodicity",
                    "action": action,
                    "weekday": weekday,
                    "hour": hour,
                    "day_name": day_names[weekday],
                    "confidence": round(confidence, 2),
                    "count": count,
                    "total": total,
                })

        return patterns

    async def mine_semantic_association(self, days: int = 30) -> list[dict]:
        """Mine semantic topic transitions from conversation history.

        Analyzes topic transitions: P(B|A) = count(A→B) / count(A)
        Uses vector similarity when embedding is available.
        """
        conversations = self._resource_store.read_recent_conversations(hours=days * 24)
        if len(conversations) < 2:
            return []

        # Extract topic keywords from each conversation
        topics = []
        for conv in conversations:
            content = conv.get("content", "")
            if content:
                topics.append(self._extract_topic(content))

        # Count transitions
        transitions: dict[tuple[str, str], int] = defaultdict(int)
        from_counts: Counter[str] = Counter()
        for i in range(len(topics) - 1):
            a, b = topics[i], topics[i + 1]
            if a and b and a != b:
                transitions[(a, b)] += 1
                from_counts[a] += 1

        patterns = []
        for (a, b), count in transitions.items():
            total = from_counts[a]
            if total < 2:
                continue
            prob = count / total
            if prob >= _SEMANTIC_ASSOCIATION_THRESHOLD:
                patterns.append({
                    "type": "semantic_association",
                    "from_topic": a,
                    "to_topic": b,
                    "confidence": round(prob, 2),
                    "count": count,
                    "total": total,
                })

        # Vector-based enhancement: find semantically similar transitions
        if self._embedding and len(conversations) >= 2:
            vector_patterns = self._mine_vector_transitions(conversations)
            # Merge: add vector patterns that don't overlap with keyword patterns
            existing_pairs = {(p["from_topic"], p["to_topic"]) for p in patterns}
            for vp in vector_patterns:
                if (vp["from_topic"], vp["to_topic"]) not in existing_pairs:
                    patterns.append(vp)

        return patterns

    def _mine_vector_transitions(self, conversations: list[dict]) -> list[dict]:
        """Find topic transitions using embedding similarity."""
        contents = [c.get("content", "") for c in conversations if c.get("content")]
        if len(contents) < 2:
            return []

        try:
            vectors = self._embedding.embed_batch(contents)
        except Exception:
            return []

        # Find high-similarity consecutive pairs
        transitions: dict[tuple[str, str], int] = defaultdict(int)
        from_counts: Counter[str] = Counter()

        for i in range(len(vectors) - 1):
            sim = self._embedding.cosine_similarity(vectors[i], vectors[i + 1])
            if sim >= 0.6:  # Related conversations
                topic_a = self._extract_topic(contents[i])
                topic_b = self._extract_topic(contents[i + 1])
                if topic_a and topic_b and topic_a != topic_b:
                    transitions[(topic_a, topic_b)] += 1
                    from_counts[topic_a] += 1

        patterns = []
        for (a, b), count in transitions.items():
            total = from_counts[a]
            if total < 2:
                continue
            prob = count / total
            if prob >= _SEMANTIC_ASSOCIATION_THRESHOLD:
                patterns.append({
                    "type": "semantic_association",
                    "from_topic": a,
                    "to_topic": b,
                    "confidence": round(prob, 2),
                    "count": count,
                    "total": total,
                    "source": "vector",
                })

        return patterns

    async def mine_entity_association(self) -> list[dict]:
        """Mine entity co-occurrence patterns from memory_item table."""
        if not self._memory_db.exists():
            return []

        try:
            conn = sqlite3.connect(str(self._memory_db))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT entities, resource_id FROM memory_item WHERE is_active = 1 AND entities != '[]'"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return []

        # Build co-occurrence graph
        co_occur: dict[tuple[str, str], int] = defaultdict(int)
        for row in rows:
            try:
                entities = json.loads(row["entities"])
            except (json.JSONDecodeError, TypeError):
                continue
            if len(entities) < 2:
                continue
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    pair = tuple(sorted([entities[i], entities[j]]))
                    co_occur[pair] += 1

        patterns = []
        for (e1, e2), count in co_occur.items():
            if count >= 2:
                patterns.append({
                    "type": "entity_association",
                    "entity_a": e1,
                    "entity_b": e2,
                    "confidence": min(1.0, count / 10),
                    "count": count,
                })

        return patterns

    async def run_daily(self) -> list[dict]:
        """Daily entry point: run all miners → filter by confidence → return structured rules.

        Output: list of rule dicts with type in (schedule, predict, associate).
        Caller is responsible for routing rules to appropriate stores.
        """
        time_patterns = await self.mine_time_periodicity()
        semantic_patterns = await self.mine_semantic_association()
        entity_patterns = await self.mine_entity_association()

        all_patterns = time_patterns + semantic_patterns + entity_patterns

        # Convert patterns to structured rules
        rules = []
        for p in all_patterns:
            rule = self._pattern_to_rule(p)
            if rule:
                rules.append(rule)

        # Sort by confidence, take top patterns
        rules.sort(key=lambda r: r.get("confidence", 0), reverse=True)
        return rules[:10]

    def _pattern_to_rule(self, pattern: dict) -> dict | None:
        """Convert a mined pattern to a structured meta-rule (not a user topic).

        Three rule types:
        - schedule: time-based triggers → registered as CronService jobs
        - predict: semantic triggers → injected into PredictionEngine
        - associate: entity co-occurrence → enhances memory context
        """
        ptype = pattern.get("type", "")
        confidence = pattern.get("confidence", 0)
        now = datetime.now(timezone.utc)

        if ptype == "time_periodicity":
            weekday = pattern.get("weekday", 0)
            hour = pattern.get("hour", 0)
            action = pattern.get("action", "")
            day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            return {
                "rule_id": f"dyn_{uuid.uuid4().hex[:8]}",
                "type": "schedule",
                "source": "pattern_miner",
                "schedule": {"kind": "cron", "expr": f"{hour} * * {weekday + 1} *"},
                "action": {"task": action, "preload": True},
                "confidence": confidence,
                "expires_at": (now + timedelta(days=30)).isoformat(),
                "created_at": now.isoformat(),
                "description": f"{day_names[weekday]} {hour}点 {action}",
            }

        if ptype == "semantic_association":
            return {
                "rule_id": f"dyn_{uuid.uuid4().hex[:8]}",
                "type": "predict",
                "source": "pattern_miner",
                "trigger": {"pattern": pattern.get("from_topic", "")},
                "action": {"preload": pattern.get("to_topic", ""), "template": f"关联推荐：{pattern.get('to_topic', '')}"},
                "confidence": confidence,
                "expires_at": (now + timedelta(days=14)).isoformat(),
                "created_at": now.isoformat(),
                "description": f"「{pattern.get('from_topic', '')}」→「{pattern.get('to_topic', '')}」关联",
            }

        if ptype == "entity_association":
            return {
                "rule_id": f"dyn_{uuid.uuid4().hex[:8]}",
                "type": "associate",
                "source": "pattern_miner",
                "entities": [pattern.get("entity_a", ""), pattern.get("entity_b", "")],
                "action": {"context_enhance": True, "template": f"{pattern.get('entity_a', '')} 与 {pattern.get('entity_b', '')} 关联"},
                "confidence": confidence,
                "expires_at": (now + timedelta(days=14)).isoformat(),
                "created_at": now.isoformat(),
                "description": f"「{pattern.get('entity_a', '')}」↔「{pattern.get('entity_b', '')}」共现",
            }

        return None

    def _extract_topic(self, text: str) -> str:
        """Extract a topic keyword from text.

        Priority: known entities > known tags > hardcoded keywords > first 4 chars.
        """
        # 1. Match against known entities from memory_item
        for entity in sorted(self._known_entities, key=len, reverse=True):
            if entity in text:
                return entity
        # 2. Match against known tags
        for tag in sorted(self._known_tags, key=len, reverse=True):
            if tag in text:
                return tag
        # 3. Fallback to hardcoded keywords
        keywords = ["旅行", "黄金", "股票", "基金", "买房", "理财", "健身", "美食", "工作", "学习"]
        for kw in keywords:
            if kw in text:
                return kw
        # 4. Last resort: first 4 chars
        return text[:4] if len(text) >= 4 else text
