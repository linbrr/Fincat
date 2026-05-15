"""QueryVectorCache — LRU cache for query embeddings.

Avoids redundant embedding computation for repeated/similar queries.
Supports both exact-match and semantic-similarity cache hits.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np


@dataclass
class _CacheEntry:
    vector: list[float]
    query: str
    created_at: float = field(default_factory=time.time)
    hit_count: int = 0


class QueryVectorCache:
    """LRU cache mapping query_hash → embedding vector.

    Features:
    - Exact-match cache (hash-based, O(1))
    - Semantic similarity hit (cosine >= threshold, scans recent entries)
    - Thread-safe for concurrent access
    - Auto-eviction of stale entries (>1h)
    """

    def __init__(self, max_size: int = 1000, similarity_threshold: float = 0.95, ttl_seconds: int = 3600):
        self._max_size = max_size
        self._similarity_threshold = similarity_threshold
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, query: str) -> list[float] | None:
        """Exact-match lookup by query hash."""
        key = self._hash(query)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() - entry.created_at > self._ttl:
                del self._cache[key]
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._hits += 1
            return entry.vector

    def similarity_hit(self, query_vec: list[float]) -> list[float] | None:
        """Scan recent entries for a semantic match (cosine >= threshold)."""
        if not query_vec:
            return None
        query_arr = np.asarray(query_vec, dtype=np.float32)
        best_score = 0.0
        best_vector = None

        with self._lock:
            now = time.time()
            for key, entry in self._cache.items():
                if now - entry.created_at > self._ttl:
                    continue
                entry_arr = np.asarray(entry.vector, dtype=np.float32)
                score = float(np.dot(query_arr, entry_arr) / (
                    np.linalg.norm(query_arr) * np.linalg.norm(entry_arr) + 1e-10
                ))
                if score > best_score:
                    best_score = score
                    best_vector = entry.vector

        if best_score >= self._similarity_threshold and best_vector is not None:
            self._hits += 1
            return best_vector
        self._misses += 1
        return None

    def put(self, query: str, vector: list[float]) -> None:
        """Insert a query→vector mapping."""
        key = self._hash(query)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key].vector = vector
                self._cache[key].created_at = time.time()
                return
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)  # evict oldest
            self._cache[key] = _CacheEntry(vector=vector, query=query)

    def invalidate(self, query: str) -> bool:
        """Remove a specific query from cache. Returns True if found."""
        key = self._hash(query)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        """Clear all cached entries. Returns count of entries removed."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def cleanup_stale(self) -> int:
        """Remove expired entries. Returns count removed."""
        now = time.time()
        with self._lock:
            stale_keys = [k for k, v in self._cache.items() if now - v.created_at > self._ttl]
            for k in stale_keys:
                del self._cache[k]
            return len(stale_keys)

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total else 0.0,
            "similarity_threshold": self._similarity_threshold,
        }

    @staticmethod
    def _hash(query: str) -> str:
        return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:16]
