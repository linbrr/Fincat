"""CategoryVectorIndex — pre-computed category summary vectors for fast Top-K matching.

Reduces the search space by first matching the query to relevant categories,
then only searching items within those categories.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np

from fincat.agent.embedding import EmbeddingEngine


@dataclass
class _CategoryEntry:
    category_id: str
    name: str
    summary: str
    vector: list[float]
    item_count: int = 0
    last_updated: float = field(default_factory=time.time)


class CategoryVectorIndex:
    """Pre-computed category summary vectors for fast Top-K matching.

    The index is built from CategoryManager's category summaries and used
    to narrow the search scope before querying MemoryStoreV2.
    """

    def __init__(self, embedding: EmbeddingEngine, threshold: float = 0.55):
        self._embedding = embedding
        self._threshold = threshold
        self._categories: dict[str, _CategoryEntry] = {}
        self._vectors: list[tuple[str, list[float]]] = []  # (category_id, vector)
        self._lock = threading.Lock()
        self._built_at: float = 0.0

    def build(self, categories: list[dict]) -> None:
        """Build the index from category metadata.

        Args:
            categories: List of dicts with keys: id, name, summary, item_count
        """
        if not categories:
            return

        summaries = [c.get("summary", c.get("name", "")) for c in categories]
        vectors = self._embedding.embed_batch(summaries)

        with self._lock:
            self._categories.clear()
            self._vectors.clear()
            for cat, vec in zip(categories, vectors):
                entry = _CategoryEntry(
                    category_id=cat["id"],
                    name=cat.get("name", cat["id"]),
                    summary=cat.get("summary", ""),
                    vector=vec,
                    item_count=cat.get("item_count", 0),
                )
                self._categories[cat["id"]] = entry
                self._vectors.append((cat["id"], vec))
            self._built_at = time.time()

    def search(self, query_vec: list[float], top_k: int = 3, threshold: float | None = None) -> list[dict]:
        """Find the top-K most relevant categories for a query vector.

        Returns:
            List of dicts: {category_id, name, score, item_count}
        """
        if not query_vec:
            return []

        threshold = threshold if threshold is not None else self._threshold
        query_arr = np.asarray(query_vec, dtype=np.float32)
        query_norm = np.linalg.norm(query_arr)
        if query_norm < 1e-10:
            return []

        results = []
        with self._lock:
            for cat_id, cat_vec in self._vectors:
                cat_arr = np.asarray(cat_vec, dtype=np.float32)
                score = float(np.dot(query_arr, cat_arr) / (query_norm * np.linalg.norm(cat_arr) + 1e-10))
                if score >= threshold:
                    entry = self._categories[cat_id]
                    results.append({
                        "category_id": cat_id,
                        "name": entry.name,
                        "score": score,
                        "item_count": entry.item_count,
                    })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get_category(self, category_id: str) -> _CategoryEntry | None:
        """Get a specific category entry by ID."""
        return self._categories.get(category_id)

    def update_category(self, category_id: str, summary: str, item_count: int = 0) -> None:
        """Update a single category's vector without rebuilding the entire index."""
        vec = self._embedding.embed(summary)
        with self._lock:
            if category_id in self._categories:
                entry = self._categories[category_id]
                entry.summary = summary
                entry.vector = vec
                entry.item_count = item_count
                entry.last_updated = time.time()
                # Update vector list
                self._vectors = [
                    (cid, vec if cid == category_id else v)
                    for cid, v in self._vectors
                ]
            else:
                entry = _CategoryEntry(
                    category_id=category_id,
                    name=category_id,
                    summary=summary,
                    vector=vec,
                    item_count=item_count,
                )
                self._categories[category_id] = entry
                self._vectors.append((category_id, vec))

    def update_category_with_vector(
        self,
        category_id: str,
        vector: list[float],
        item_count: int = 0,
        name: str | None = None,
    ) -> None:
        """Update a single category using a pre-computed vector (no re-embedding)."""
        with self._lock:
            if category_id in self._categories:
                entry = self._categories[category_id]
                entry.vector = vector
                entry.item_count = item_count
                entry.last_updated = time.time()
                if name:
                    entry.name = name
                self._vectors = [
                    (cid, vector if cid == category_id else v)
                    for cid, v in self._vectors
                ]
            else:
                entry = _CategoryEntry(
                    category_id=category_id,
                    name=name or category_id,
                    summary="",
                    vector=vector,
                    item_count=item_count,
                )
                self._categories[category_id] = entry
                self._vectors.append((category_id, vector))

    def remove_category(self, category_id: str) -> bool:
        """Remove a category from the index."""
        with self._lock:
            if category_id not in self._categories:
                return False
            del self._categories[category_id]
            self._vectors = [(cid, v) for cid, v in self._vectors if cid != category_id]
            return True

    @property
    def size(self) -> int:
        return len(self._categories)

    @property
    def built_at(self) -> float:
        return self._built_at

    @property
    def category_ids(self) -> list[str]:
        return list(self._categories.keys())

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._categories),
            "threshold": self._threshold,
            "built_at": self._built_at,
            "categories": [
                {"id": e.category_id, "name": e.name, "items": e.item_count}
                for e in self._categories.values()
            ],
        }
