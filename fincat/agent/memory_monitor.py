"""MemoryMonitor — proactive Trigger & Monitor engine for MemoryItems.

Runs periodic scans over MemoryItemStore, checking trigger conditions using
pure CPU rules (0 token). Only pattern/trend analysis uses LLM (once daily).

Trigger types:
  - frequency: same content appears N times → mark as high-priority
  - time: periodic event reaches its scheduled time → reminder
  - threshold: decay_score falls below threshold → confirm validity
  - pattern: behavior pattern change detected → alert
  - event: external event occurs → notify interested users

Alert levels:
  - remind: send proactive message to user
  - confirm: ask user to verify
  - archive: move low-decay item to history
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from loguru import logger

from fincat.agent.memory_item import MemoryItem, MemoryItemStore


class TriggerKind(str, Enum):
    FREQUENCY = "frequency"       # frequency >= N
    TIME = "time"                 # timestamp + period <= now
    THRESHOLD = "threshold"       # decay_score < min
    PATTERN = "pattern"           # behavior change
    EVENT = "event"               # external event


class AlertLevel(str, Enum):
    REMIND = "remind"             # proactive message
    CONFIRM = "confirm"           # ask user to verify
    ARCHIVE = "archive"           # move to history


@dataclass
class Trigger:
    """A trigger condition bound to a MemoryItem."""
    kind: TriggerKind
    item: MemoryItem | None       # None for PATTERN/EVENT triggers
    alert: AlertLevel
    message: str
    fired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Trigger checkers — pure CPU, 0 token
# ---------------------------------------------------------------------------


def check_frequency(item: MemoryItem, threshold: int = 3) -> tuple[bool, str]:
    """Fire when an item has been observed *threshold* or more times."""
    if item.frequency >= threshold:
        return True, f"高频记忆（出现 {item.frequency} 次）: {item.content}"
    return False, ""


def check_decay_threshold(item: MemoryItem, min_score: float = 0.2, days: int = 60) -> tuple[bool, str]:
    """Fire when decay_score drops below *min_score* or item is older than *days*."""
    now = datetime.now(timezone.utc)
    item.compute_decay(now=now)
    if item.decay_score < min_score:
        return True, f"记忆衰减（decay={item.decay_score:.2f}），是否需要保留？: {item.content}"
    ts = item.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_days = (now - ts).total_seconds() / 86400.0
    if age_days > days:
        return True, f"记忆已超过 {days} 天（{age_days:.0f} 天），建议归档: {item.content}"
    return False, ""


def check_periodic(item: MemoryItem, period_days: int = 30) -> tuple[bool, str]:
    """Fire when *period_days* have elapsed since the item's timestamp."""
    ts = item.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed = (now - ts).total_seconds() / 86400.0
    if elapsed >= period_days:
        return True, f"周期性提醒（距上次 {elapsed:.0f} 天）: {item.content}"
    return False, ""


# ---------------------------------------------------------------------------
# PATTERN trigger — behavior shift detection (pure CPU, 0 token)
# ---------------------------------------------------------------------------


def _extract_categories(items: list[MemoryItem]) -> dict[str, int]:
    """Count items per category."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.category] = counts.get(item.category, 0) + 1
    return counts


def _extract_top_entities(items: list[MemoryItem], limit: int = 20) -> list[str]:
    """Extract top-N entities by mention frequency using regex patterns."""
    from fincat.agent.pattern_snapshot import extract_entities_from_text
    entity_freq: dict[str, int] = {}
    for item in items:
        for entity in extract_entities_from_text(item.content):
            entity_freq[entity] = entity_freq.get(entity, 0) + item.frequency
    sorted_entities = sorted(entity_freq.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in sorted_entities[:limit]]


def check_pattern(
    current_items: list[MemoryItem],
    snapshot: Any | None,
) -> list[tuple[bool, str]]:
    """Rule-based pattern change detection. 0 LLM cost.

    Compares current MemoryItem distribution against a stored snapshot.
    Returns list of (fired, message) tuples.
    """
    if snapshot is None:
        return []

    results: list[tuple[bool, str]] = []

    # 1. Category distribution shift detection (>20 percentage points)
    current_counts = _extract_categories(current_items)
    total = sum(current_counts.values()) or 1
    for cat, count in current_counts.items():
        old_count = snapshot.category_counts.get(cat, 0)
        old_total = sum(snapshot.category_counts.values()) or 1
        shift = (count / total) - (old_count / old_total)
        if abs(shift) > 0.20:
            direction = "增加" if shift > 0 else "减少"
            results.append((True,
                f"行为模式变化: {cat} 类记忆占比{direction}了 {abs(shift)*100:.0f}%"))

    # 2. New entity cluster detection (3+ new top-20 entities)
    current_entities = _extract_top_entities(current_items, limit=20)
    new_entities = set(current_entities) - set(snapshot.top_entities)
    if len(new_entities) >= 3:
        examples = ", ".join(list(new_entities)[:5])
        results.append((True, f"新增关注实体: {examples}"))

    # 3. Frequency anomaly detection (item freq > 2x category average)
    category_avg: dict[str, float] = {}
    for cat, count in current_counts.items():
        cat_items = [i for i in current_items if i.category == cat]
        if cat_items:
            category_avg[cat] = sum(i.frequency for i in cat_items) / len(cat_items)
    for item in current_items:
        avg = category_avg.get(item.category, 1)
        if avg > 0 and item.frequency >= 2 * avg and item.frequency >= 3:
            results.append((True,
                f"频率异常: 「{item.content[:30]}」出现 {item.frequency} 次（{item.category} 均值 {avg:.1f}）"))

    return results


# ---------------------------------------------------------------------------
# EVENT trigger — external event matching (pure CPU, 0 token)
# ---------------------------------------------------------------------------


def check_event(
    event: Any,
    items: list[MemoryItem],
    entity_graph: Any | None = None,
) -> tuple[bool, str]:
    """Check if an external event is relevant to a user's memory items.

    Matching strategy:
    1. Direct entity match
    2. Entity graph propagation (multi-hop via Phase 2 relations)
    3. Category-level match (e.g. regulation_change → compliance items)
    """
    from fincat.agent.pattern_snapshot import extract_entities_from_text

    # Collect all entities from user's memory items
    item_entities: set[str] = set()
    for item in items:
        item_entities.update(extract_entities_from_text(item.content))

    # 1. Direct entity match
    event_entities = set(event.entities)
    matched = event_entities & item_entities
    if matched:
        return True, f"外部事件关联您的关注: {event.title}"

    # 2. Entity graph propagation (multi-hop)
    if entity_graph and hasattr(entity_graph, "get_entity_neighbors"):
        for event_entity in event.entities:
            try:
                neighbors = entity_graph.get_entity_neighbors(event_entity)
                for n in neighbors:
                    nid = n.get("entity_id", "")
                    nname = n.get("name", "")
                    if nid in item_entities or nname in item_entities:
                        return True, f"外部事件间接影响您的关注 ({nname}): {event.title}"
            except Exception:
                continue

    # 3. Category-level match
    if event.event_type == "regulation_change":
        if any(i.category == "compliance" for i in items):
            return True, f"监管政策变化可能影响您: {event.title}"

    return False, ""


# ---------------------------------------------------------------------------
# MemoryMonitor
# ---------------------------------------------------------------------------


class MemoryMonitor:
    """Periodic scanner over MemoryItemStore. Checks trigger conditions and
    produces a list of Trigger objects for the agent to act on.

    All checks are pure CPU — no LLM calls. Use *analyze_trends()* (LLM call)
    for daily pattern analysis, or *scan_with_trends()* for the full pipeline.
    """

    # Default thresholds
    FREQUENCY_THRESHOLD = 3
    DECAY_MIN_SCORE = 0.2
    DECAY_MAX_DAYS = 60
    PERIODIC_DAYS = 30

    def __init__(
        self,
        item_store: MemoryItemStore,
        *,
        frequency_threshold: int = FREQUENCY_THRESHOLD,
        decay_min_score: float = DECAY_MIN_SCORE,
        decay_max_days: int = DECAY_MAX_DAYS,
        periodic_days: int = PERIODIC_DAYS,
        on_trigger: Callable[[Trigger], None] | None = None,
        snapshot_store: Any | None = None,
        event_queue: Any | None = None,
        knowledge_store: Any | None = None,
    ):
        self._store = item_store
        self.frequency_threshold = frequency_threshold
        self.decay_min_score = decay_min_score
        self.decay_max_days = decay_max_days
        self.periodic_days = periodic_days
        self._on_trigger = on_trigger
        self._last_scan: datetime | None = None

        # Phase 3 components
        self._snapshot_store = snapshot_store
        self._event_queue = event_queue
        self._knowledge_store = knowledge_store
        self._last_trend_analysis: datetime | None = None

    # -- main scan ----------------------------------------------------------

    def scan(self) -> list[Trigger]:
        """Run a full scan over all MemoryItems. Returns fired triggers."""
        self._last_scan = datetime.now(timezone.utc)
        triggers: list[Trigger] = []

        for item in self._store.all():
            item_triggers = self._check_item(item)
            for trigger in item_triggers:
                triggers.append(trigger)
                if self._on_trigger:
                    try:
                        self._on_trigger(trigger)
                    except Exception:
                        logger.exception("on_trigger callback failed")

        # PATTERN trigger: rule-based pattern detection
        pattern_triggers = self._check_pattern_triggers()
        for trigger in pattern_triggers:
            triggers.append(trigger)
            if self._on_trigger:
                try:
                    self._on_trigger(trigger)
                except Exception:
                    logger.exception("on_trigger callback failed")

        if triggers:
            logger.info(
                "MemoryMonitor: {} trigger(s) fired across {} items",
                len(triggers), len(self._store),
            )
        return triggers

    async def scan_events(self) -> list[Trigger]:
        """Drain the event queue and check each event against user items."""
        if not self._event_queue:
            return []

        triggers: list[Trigger] = []
        items = self._store.all()

        while True:
            event = self._event_queue.pop_sync()
            if event is None:
                break

            fired, msg = check_event(event, items, self._knowledge_store)
            if fired:
                trigger = Trigger(
                    kind=TriggerKind.EVENT,
                    item=None,
                    alert=AlertLevel.REMIND,
                    message=msg,
                    metadata={"event": event.to_dict()},
                )
                triggers.append(trigger)
                if self._on_trigger:
                    try:
                        self._on_trigger(trigger)
                    except Exception:
                        logger.exception("on_trigger callback failed")

        if triggers:
            logger.info("MemoryMonitor: {} EVENT trigger(s) fired", len(triggers))
        return triggers

    async def scan_with_trends(
        self,
        provider: Any,
        model: str,
    ) -> list[Trigger]:
        """Full scan + daily trend analysis + snapshot creation.

        Only runs LLM analysis once per 24 hours.
        """
        # Run standard scan
        triggers = self.scan()

        # Gate: only run trend analysis once per 24 hours
        now = datetime.now(timezone.utc)
        if self._last_trend_analysis:
            elapsed_hours = (now - self._last_trend_analysis).total_seconds() / 3600
            if elapsed_hours < 24:
                return triggers

        # Run LLM trend analysis
        analysis = await self.analyze_trends(provider, model)
        self._last_trend_analysis = now

        if analysis and self._snapshot_store:
            # Build and save snapshot with LLM analysis
            from fincat.agent.pattern_snapshot import build_snapshot
            snapshot = build_snapshot(self._store.all())
            snapshot.trend_analysis = analysis
            self._snapshot_store.save(snapshot)

            # Generate a PATTERN trigger from LLM analysis
            trigger = Trigger(
                kind=TriggerKind.PATTERN,
                item=None,
                alert=AlertLevel.REMIND,
                message=f"每日趋势分析:\n{analysis}",
                metadata={"source": "llm_trend_analysis"},
            )
            triggers.append(trigger)

        return triggers

    def _check_pattern_triggers(self) -> list[Trigger]:
        """Rule-based pattern triggers from snapshot comparison."""
        if not self._snapshot_store:
            return []

        snapshot = self._snapshot_store.load_latest()
        if not snapshot:
            return []

        items = self._store.all()
        fired_list = check_pattern(items, snapshot)

        return [
            Trigger(
                kind=TriggerKind.PATTERN,
                item=None,
                alert=AlertLevel.REMIND,
                message=msg,
                metadata={"source": "rule_based"},
            )
            for fired, msg in fired_list
            if fired
        ]

    def _check_item(self, item: MemoryItem) -> list[Trigger]:
        """Run all trigger checks on a single item.

        Note: frequency and periodic triggers removed — replaced by PatternMiner.
        Only decay threshold trigger remains for archive management.
        """
        result: list[Trigger] = []

        # Decay / age threshold trigger
        fired, msg = check_decay_threshold(item, self.decay_min_score, self.decay_max_days)
        if fired:
            # If very decayed, suggest archive
            item.compute_decay()
            alert = AlertLevel.ARCHIVE if item.decay_score < 0.1 else AlertLevel.CONFIRM
            result.append(Trigger(
                kind=TriggerKind.THRESHOLD, item=item,
                alert=alert, message=msg,
            ))

        return result

    # -- targeted queries ---------------------------------------------------

    def query_category(self, category: str) -> list[MemoryItem]:
        """Return all items in a category, sorted by decay_score desc."""
        return self._store.query(category=category)

    def get_decayed_items(self, min_score: float | None = None) -> list[MemoryItem]:
        """Return items with decay_score below threshold."""
        threshold = min_score or self.decay_min_score
        # Filter items where decay_score < threshold (the truly decayed ones)
        all_items = self._store.all()
        result = [it for it in all_items if it.decay_score < threshold]
        result.sort(key=lambda it: it.decay_score, reverse=True)
        return result

    def get_high_frequency_items(self, min_freq: int | None = None) -> list[MemoryItem]:
        """Return items with frequency >= *min_freq*."""
        return self._store.query(min_frequency=min_freq or self.frequency_threshold)

    # -- trend analysis (LLM call, ~500 token, once daily) -------------------

    async def analyze_trends(
        self,
        provider: Any,
        model: str,
        category: str | None = None,
    ) -> str | None:
        """Run LLM-based trend analysis on MemoryItems.

        Called once daily. Uses ~500 tokens.
        Returns analysis text or None on failure.
        """
        items = self._store.query(category=category, limit=100)
        if len(items) < 3:
            return None

        items_text = "\n".join(
            f"[{it.timestamp.strftime('%Y-%m-%d')}] [{it.category}] freq={it.frequency} decay={it.decay_score:.2f} {it.content}"
            for it in sorted(items, key=lambda i: i.timestamp)
        )

        prompt = f"""Analyze the following user memory items and identify trends:

{items_text}

Output a concise trend summary in Chinese:
1. 偏好变化（如风险偏好、产品关注、沟通风格的转变）
2. 情绪趋势（如正面→犹豫→负面的变化）
3. 重复关注（同一话题反复出现，可能表示未解决问题）
4. 建议行动（基于趋势，建议的主动服务动作）

Keep under 300 words."""

        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                tool_choice=None,
            )
            result = response.content or ""
            logger.info("MemoryMonitor trend analysis complete ({} chars)", len(result))
            return result
        except Exception as e:
            logger.warning("MemoryMonitor trend analysis failed: {}", e)
            return None

    @property
    def last_scan(self) -> datetime | None:
        return self._last_scan

    @property
    def item_count(self) -> int:
        return len(self._store)
