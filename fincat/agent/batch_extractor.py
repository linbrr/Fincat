"""BatchExtractor — P1/P2/P3 三级记忆提取引擎."""

from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

from fincat.agent.memory_store_v2 import MemoryStoreV2
from fincat.agent.resource_store import ResourceStore

_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "agent" / "batch_extract.md"

# Extraction thresholds
_BATCH_POOL_SIZE = 5          # P2: flush when pool reaches N items
_BATCH_INTERVAL_SEC = 60      # P2: flush if >1 min since last batch
_MAX_RETRIES = 3               # P3: max retries for failed resources


class BatchExtractor:
    """三级提取引擎：P1 即时 / P2 增量批量 / P3 兜底."""

    def __init__(
        self,
        store: MemoryStoreV2,
        provider,
        model: str,
        resource_store: ResourceStore,
        category_manager=None,
    ):
        self._store = store
        self._provider = provider
        self._model = model
        self._resource_store = resource_store
        self._category_manager = category_manager

        self._pending_pool: list[dict] = []
        self._last_batch_time: float = 0.0
        self._failed_resources: list[tuple[dict, int]] = []  # (resource, retry_count)

    # ------------------------------------------------------------------
    # P1: Immediate extraction
    # ------------------------------------------------------------------

    async def extract_immediate(self, resource: dict) -> list[str]:
        """User explicitly asks to remember / high-value info. Returns item_ids."""
        logger.info("BatchExtractor: P1 immediate extraction for {}", resource.get("resource_id"))
        return await self._extract_and_save([resource])

    # ------------------------------------------------------------------
    # P2: Incremental batch
    # ------------------------------------------------------------------

    def add_to_pending(self, resource: dict) -> None:
        self._pending_pool.append(resource)
        logger.debug("BatchExtractor: added to pending pool ({})", len(self._pending_pool))

    def should_flush(self) -> bool:
        if len(self._pending_pool) >= _BATCH_POOL_SIZE:
            return True
        if self._pending_pool and (time.time() - self._last_batch_time) > _BATCH_INTERVAL_SEC:
            return True
        return False

    async def flush_pending(self) -> list[str]:
        """Flush the pending pool. Returns item_ids."""
        if not self._pending_pool:
            return []
        resources = list(self._pending_pool)
        self._pending_pool.clear()
        self._last_batch_time = time.time()
        logger.info("BatchExtractor: P2 batch extraction for {} resources", len(resources))
        return await self._extract_and_save(resources)

    # ------------------------------------------------------------------
    # P3: Fallback (failed resources)
    # ------------------------------------------------------------------

    async def extract_pending(self) -> list[str]:
        """Process previously failed resources. Returns item_ids."""
        if not self._failed_resources:
            return []
        to_retry = []
        remaining = []
        for resource, retries in self._failed_resources:
            if retries < _MAX_RETRIES:
                to_retry.append(resource)
            else:
                logger.warning("BatchExtractor: giving up on {}", resource.get("resource_id"))
        self._failed_resources = remaining

        if not to_retry:
            return []
        logger.info("BatchExtractor: P3 fallback for {} resources", len(to_retry))
        return await self._extract_and_save(to_retry)

    # ------------------------------------------------------------------
    # Core extraction
    # ------------------------------------------------------------------

    async def _extract_and_save(self, resources: list[dict]) -> list[str]:
        """Build prompt → call LLM → dedup → save. Returns item_ids."""
        prompt = self._build_prompt(resources)
        try:
            response = await self._provider.chat(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            extracted = self._parse_response(response.content or "")
        except Exception as e:
            logger.error("BatchExtractor: LLM extraction failed: {}", e)
            for r in resources:
                self._failed_resources.append((r, 0))
            return []

        return await self._dedup_and_save(extracted)

    async def _dedup_and_save(self, extracted: list[dict]) -> list[str]:
        """Vector dedup → save to memory_item → embed → assign category."""
        item_ids = []
        for item_data in extracted:
            summary = item_data.get("summary", "")
            if not summary:
                continue

            # Vector dedup
            similar = self._store.search_similar(summary, threshold=0.9)
            if similar:
                self._store.touch_item(similar["item_id"])
                logger.debug("BatchExtractor: dedup hit for '{}' → {}", summary, similar["item_id"])
                continue

            # Save to memory_item
            item_id = self._store.add_item(
                resource_id=item_data.get("resource_id", ""),
                memory_type=item_data.get("memory_type", "fact"),
                summary=summary,
                content=item_data.get("content", ""),
                importance_score=item_data.get("importance_score", 0.5),
                entities=item_data.get("entities", []),
                tags=item_data.get("tags", []),
            )

            # Embed and index
            try:
                self._store.embed_and_index(item_id, summary)
            except Exception as e:
                logger.error("BatchExtractor: embedding failed for {}: {}", item_id, e)

            # Assign category (if category_manager available)
            if self._category_manager:
                try:
                    category_id = self._category_manager.find_best_category(
                        summary, item_data.get("memory_type", "fact")
                    )
                    if category_id:
                        self._store.update_item(item_id, category_id=category_id)
                        self._category_manager.add_item_to_category(
                            category_id, item_id,
                            {"memory_type": item_data.get("memory_type", "fact"), "summary": summary},
                        )
                except Exception as e:
                    logger.error("BatchExtractor: category assignment failed for {}: {}", item_id, e)

            # Update resource's related_item_ids
            resource_id = item_data.get("resource_id", "")
            if resource_id:
                resource = self._resource_store.get_by_resource_id(resource_id)
                if resource:
                    existing = resource.get("related_item_ids", [])
                    existing.append(item_id)
                    self._resource_store.update_related_items(resource_id, existing)

            item_ids.append(item_id)
            logger.debug("BatchExtractor: saved item {} ({})", item_id, summary)

        return item_ids

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_prompt(self, resources: list[dict]) -> str:
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")
        resources_json = json.dumps(
            [{"resource_id": r.get("resource_id"), "content": r.get("content")} for r in resources],
            ensure_ascii=False,
            indent=2,
        )
        return template.replace("{{resources}}", resources_json)

    @staticmethod
    def _parse_response(response: str) -> list[dict]:
        """Parse LLM JSON response, handling markdown code blocks."""
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            return []
        except json.JSONDecodeError:
            logger.error("BatchExtractor: failed to parse LLM response as JSON")
            return []
