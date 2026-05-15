"""Event sources and queue for the EVENT trigger system.

External events (market moves, new documents, manual pushes) enter
the system through EventSource adapters and are consumed by
MemoryMonitor.check_event().
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from loguru import logger


@dataclass
class ExternalEvent:
    """An external event that may be relevant to a user's interests."""

    event_id: str
    event_type: str  # "market_move" / "new_document" / "regulation_change" / "custom"
    title: str
    summary: str
    entities: list[str]  # canonical entity_ids
    severity: str  # "info" / "warning" / "critical"
    source: str  # "akshare" / "document_ingest" / "manual"
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "title": self.title,
            "summary": self.summary,
            "entities": self.entities,
            "severity": self.severity,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Event Queue — bounded async queue
# ---------------------------------------------------------------------------


class EventQueue:
    """Bounded async queue for ExternalEvents."""

    def __init__(self, max_size: int = 100):
        self._queue: asyncio.Queue[ExternalEvent] = asyncio.Queue(maxsize=max_size)
        self._max_size = max_size

    async def push(self, event: ExternalEvent) -> bool:
        """Push an event into the queue. Returns False if queue is full."""
        try:
            self._queue.put_nowait(event)
            logger.debug("EventQueue: pushed {} ({})", event.event_type, event.title[:40])
            return True
        except asyncio.QueueFull:
            logger.warning("EventQueue full ({}), dropping event: {}", self._max_size, event.title[:40])
            return False

    async def pop(self) -> ExternalEvent | None:
        """Pop the next event, or None if empty."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def pop_sync(self) -> ExternalEvent | None:
        """Synchronous pop for use in non-async contexts."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def pending_count(self) -> int:
        return self._queue.qsize()


# ---------------------------------------------------------------------------
# Event Source Protocol
# ---------------------------------------------------------------------------


class EventSource(Protocol):
    """Protocol for event source adapters."""

    async def poll(self) -> list[ExternalEvent]:
        """Poll for new events. Returns empty list if none."""
        ...


# ---------------------------------------------------------------------------
# MarketEventSource — AKShare polling
# ---------------------------------------------------------------------------


class MarketEventSource:
    """Polls AKShare for significant market moves.

    Thresholds:
    - Individual stocks: price change > 5%
    - Indices: price change > 2%
    """

    STOCK_THRESHOLD = 5.0
    INDEX_THRESHOLD = 2.0

    def __init__(self, entity_aliases: dict[str, str] | None = None):
        self._aliases = entity_aliases or {}
        self._seen: set[str] = set()  # dedup within a polling cycle

    def reset_seen(self) -> None:
        """Reset dedup set (call at start of each polling cycle)."""
        self._seen.clear()

    async def poll(self) -> list[ExternalEvent]:
        """Poll AKShare for significant market moves."""
        events: list[ExternalEvent] = []
        now = datetime.now(timezone.utc)

        try:
            import akshare as ak

            # Individual stocks
            try:
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        pct = float(row.get("涨跌幅", 0))
                        if abs(pct) >= self.STOCK_THRESHOLD:
                            code = str(row.get("代码", ""))
                            name = str(row.get("名称", ""))
                            key = f"stock_{code}_{now.strftime('%Y%m%d')}"
                            if key in self._seen:
                                continue
                            self._seen.add(key)

                            direction = "涨幅" if pct > 0 else "跌幅"
                            entity_id = self._resolve_entity(code, name)
                            events.append(ExternalEvent(
                                event_id=str(uuid.uuid4())[:8],
                                event_type="market_move",
                                title=f"{name}({code}){direction}超过{abs(pct):.1f}%",
                                summary=f"{name}今日{direction}{abs(pct):.1f}%，当前行情异动。",
                                entities=[entity_id],
                                severity="warning" if abs(pct) >= 8 else "info",
                                source="akshare",
                                timestamp=now,
                                metadata={"code": code, "pct_change": pct},
                            ))
            except Exception as e:
                logger.debug("AKShare stock poll failed: {}", e)

            # Indices
            try:
                df_idx = ak.stock_zh_index_spot_em()
                if df_idx is not None and not df_idx.empty:
                    for _, row in df_idx.iterrows():
                        pct = float(row.get("涨跌幅", 0))
                        if abs(pct) >= self.INDEX_THRESHOLD:
                            code = str(row.get("代码", ""))
                            name = str(row.get("名称", ""))
                            key = f"index_{code}_{now.strftime('%Y%m%d')}"
                            if key in self._seen:
                                continue
                            self._seen.add(key)

                            direction = "涨幅" if pct > 0 else "跌幅"
                            events.append(ExternalEvent(
                                event_id=str(uuid.uuid4())[:8],
                                event_type="market_move",
                                title=f"{name}{direction}超过{abs(pct):.1f}%",
                                summary=f"指数{name}今日{direction}{abs(pct):.1f}%。",
                                entities=[f"index_{code}"],
                                severity="warning" if abs(pct) >= 4 else "info",
                                source="akshare",
                                timestamp=now,
                                metadata={"code": code, "pct_change": pct},
                            ))
            except Exception as e:
                logger.debug("AKShare index poll failed: {}", e)

        except ImportError:
            logger.debug("akshare not installed, MarketEventSource disabled")

        if events:
            logger.info("MarketEventSource: {} event(s) detected", len(events))
        return events

    def _resolve_entity(self, code: str, name: str) -> str:
        """Resolve stock code/name to canonical entity_id via aliases."""
        for alias, canonical in self._aliases.items():
            if alias == code or alias == name:
                return canonical
        return f"stock_{code}"


# ---------------------------------------------------------------------------
# DocumentEventSource — knowledge store polling
# ---------------------------------------------------------------------------


class DocumentEventSource:
    """Detects new documents added to the knowledge store."""

    def __init__(self, knowledge_store: Any = None):
        self._store = knowledge_store
        self._last_check: datetime | None = None

    async def poll(self) -> list[ExternalEvent]:
        """Check for new documents since last poll."""
        if not self._store:
            return []

        now = datetime.now(timezone.utc)
        since = self._last_check or datetime(2020, 1, 1, tzinfo=timezone.utc)
        self._last_check = now

        events: list[ExternalEvent] = []
        try:
            if hasattr(self._store, "get_recent_documents"):
                docs = self._store.get_recent_documents(since)
                for doc in docs:
                    events.append(ExternalEvent(
                        event_id=str(uuid.uuid4())[:8],
                        event_type="new_document",
                        title=f"新文档入库: {doc.title}",
                        summary=f"知识库新增文档「{doc.title}」（类别: {doc.category}）",
                        entities=[],
                        severity="info",
                        source="document_ingest",
                        timestamp=now,
                        metadata={"doc_id": doc.doc_id, "category": doc.category},
                    ))
        except Exception as e:
            logger.debug("DocumentEventSource poll failed: {}", e)

        if events:
            logger.info("DocumentEventSource: {} new document(s)", len(events))
        return events


# ---------------------------------------------------------------------------
# ManualEventSource — programmatic push
# ---------------------------------------------------------------------------


class ManualEventSource:
    """Allows programmatic event injection via the event queue."""

    def __init__(self, queue: EventQueue):
        self._queue = queue

    async def push_event(
        self,
        title: str,
        summary: str,
        entities: list[str] | None = None,
        event_type: str = "custom",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Push a custom event into the queue."""
        event = ExternalEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=event_type,
            title=title,
            summary=summary,
            entities=entities or [],
            severity=severity,
            source="manual",
            timestamp=datetime.now(timezone.utc),
            metadata=metadata or {},
        )
        return await self._queue.push(event)
