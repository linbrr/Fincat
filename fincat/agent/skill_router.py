"""SkillRouter — 4-layer skill pre-retrieval system.

Narrows 15+ skills down to 1-3 candidates before handing off to the LLM,
reducing token consumption and improving selection accuracy.

Layers:
  1. Rule matching (slash commands, keywords, activate_when)
  2. Semantic vector retrieval (top-5, similarity >= 0.7)
  3. Context & historical behavior filtering (stale, requirements)
  4. Scoring & ranking (keep top 3)

Vector cache: persisted to `.vector_cache.json` in the skills directory.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from fincat.agent.embedding import EmbeddingEngine
    from fincat.agent.query_cache import QueryVectorCache
    from fincat.agent.query_preprocessor import QueryPreprocessor
    from fincat.agent.skill_tracker import SkillUsageTracker
    from fincat.agent.skills import SkillsLoader

logger = logging.getLogger(__name__)

# Scoring weights for Layer 4
_WEIGHT_SIMILARITY = 0.40
_WEIGHT_RULE_MATCH = 0.25
_WEIGHT_QUALITY = 0.20
_WEIGHT_SESSION_RECENCY = 0.15

_MIN_SCORE = 0.3
_MAX_CANDIDATES = 3
_VECTOR_SEARCH_TOP_K = 5
_VECTOR_SEARCH_THRESHOLD = 0.7


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SkillCandidate:
    name: str
    score: float
    source: str  # "rule" | "semantic" | "combined"
    similarity: float
    quality_score: float
    description: str


@dataclass
class SkillRoutingResult:
    always_skills: list[str]
    layer1_matches: list[str]
    candidates: list[SkillCandidate]
    all_available: list[str]


# ---------------------------------------------------------------------------
# SkillVectorIndex — persistent JSON-backed skill vector store
# ---------------------------------------------------------------------------

class SkillVectorIndex:
    """Pre-computed skill vectors with JSON persistence.

    Storage: ``<skills_dir>/.vector_cache.json``
    Format: ``{skill_name: [float, ...], ...}``

    Lifecycle:
      - Startup: load cache → scan skills → embed missing → write back
      - Runtime search: in-memory cosine similarity (no disk I/O)
      - Skill change: update memory + write back to JSON
    """

    def __init__(self, embedding: EmbeddingEngine, cache_path: Path):
        self._embedding = embedding
        self._cache_path = cache_path
        self._vectors: dict[str, list[float]] = {}
        self._content_hashes: dict[str, str] = {}
        self._lock = threading.Lock()

    def build(self, skills: list[dict[str, str]], loader: SkillsLoader) -> None:
        """Build index from skill list. Loads cached vectors, embeds missing ones.

        Args:
            skills: List of skill info dicts with 'name', 'path' keys.
            loader: SkillsLoader to read skill content.
        """
        # Step 1: Load existing cache from disk
        cached = self._load_cache()

        # Step 2: Determine which skills need (re-)embedding
        to_embed_names: list[str] = []
        to_embed_texts: list[str] = []

        with self._lock:
            for entry in skills:
                name = entry["name"]
                content = loader.load_skill(name, inject_content=False) or ""
                content_hash = self._content_hash(content)

                if name in cached and cached[name].get("hash") == content_hash:
                    # Cache hit — reuse stored vector
                    self._vectors[name] = cached[name]["vector"]
                    self._content_hashes[name] = content_hash
                else:
                    # Cache miss or content changed — need embedding
                    to_embed_names.append(name)
                    to_embed_texts.append(self._extract_embedding_text(name, content))
                    self._content_hashes[name] = content_hash

        # Step 3: Batch embed missing skills
        if to_embed_texts:
            vectors = self._embedding.embed_batch(to_embed_texts)
            with self._lock:
                for name, vec in zip(to_embed_names, vectors):
                    self._vectors[name] = vec

        # Step 4: Write updated cache to disk
        self._save_cache()

        logger.info(
            "SkillVectorIndex built: %d total, %d from cache, %d newly embedded",
            len(skills), len(skills) - len(to_embed_texts), len(to_embed_texts),
        )

    def search(self, query_vec: list[float], top_k: int = _VECTOR_SEARCH_TOP_K,
               threshold: float = _VECTOR_SEARCH_THRESHOLD) -> list[dict]:
        """Find top-K skills by cosine similarity.

        Returns list of dicts: {name, score}
        """
        if not query_vec:
            return []

        query_arr = np.asarray(query_vec, dtype=np.float32)
        query_norm = np.linalg.norm(query_arr)
        if query_norm < 1e-10:
            return []

        results = []
        with self._lock:
            for name, vec in self._vectors.items():
                vec_arr = np.asarray(vec, dtype=np.float32)
                score = float(np.dot(query_arr, vec_arr) / (query_norm * np.linalg.norm(vec_arr) + 1e-10))
                if score >= threshold:
                    results.append({"name": name, "score": score})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def upsert(self, skill_name: str, content: str) -> None:
        """Add or update a single skill vector (memory + disk)."""
        text = self._extract_embedding_text(skill_name, content)
        vec = self._embedding.embed(text)
        content_hash = self._content_hash(content)
        with self._lock:
            self._vectors[skill_name] = vec
            self._content_hashes[skill_name] = content_hash
        self._save_cache()

    def remove(self, skill_name: str) -> None:
        """Remove a single skill vector (memory + disk)."""
        with self._lock:
            self._vectors.pop(skill_name, None)
            self._content_hashes.pop(skill_name, None)
        self._save_cache()

    def invalidate(self, skills: list[dict[str, str]] | None = None,
                   loader: SkillsLoader | None = None) -> None:
        """Full rebuild. If skills/loader not provided, just clears vectors."""
        if skills is not None and loader is not None:
            self.build(skills, loader)
        else:
            with self._lock:
                self._vectors.clear()
                self._content_hashes.clear()

    @property
    def size(self) -> int:
        return len(self._vectors)

    # -- internal -----------------------------------------------------------

    def _load_cache(self) -> dict:
        """Load vector cache from JSON file."""
        if not self._cache_path.exists():
            return {}
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load skill vector cache: %s", e)
        return {}

    def _save_cache(self) -> None:
        """Write current vectors to JSON file."""
        with self._lock:
            data = {
                name: {"vector": vec, "hash": self._content_hashes.get(name, "")}
                for name, vec in self._vectors.items()
            }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to save skill vector cache: %s", e)

    @staticmethod
    def _content_hash(content: str) -> str:
        """SHA-256 hash of skill content for cache validation."""
        import hashlib
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _extract_embedding_text(name: str, content: str) -> str:
        """Extract embeddable text from skill: description + trigger phrases."""
        # Extract description from frontmatter
        desc_match = re.search(r'^description:\s*["\']?(.*?)["\']?\s*$', content, re.MULTILINE)
        description = desc_match.group(1).strip() if desc_match else ""

        # Remove frontmatter for trigger extraction
        body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
        # Extract trigger phrases (Chinese quotes)
        triggers = re.findall(r'["""]([^"""]+)["""]', body)

        parts = [name, description] + triggers
        return " ".join(p for p in parts if p).lower()


# ---------------------------------------------------------------------------
# SkillRouter
# ---------------------------------------------------------------------------

class SkillRouter:
    """4-layer skill pre-retrieval system.

    Usage::

        router = SkillRouter(skills_loader, embedding, usage_tracker, query_cache, query_preprocessor)
        result = router.route(user_message="分析一下茅台", session_key="default")
        # result.candidates → 1-3 SkillCandidate objects
    """

    def __init__(
        self,
        skills_loader: SkillsLoader,
        embedding: EmbeddingEngine,
        usage_tracker: SkillUsageTracker | None = None,
        query_cache: QueryVectorCache | None = None,
        query_preprocessor: QueryPreprocessor | None = None,
        workspace_skills_dir: Path | None = None,
    ):
        self._loader = skills_loader
        self._embedding = embedding
        self._tracker = usage_tracker
        self._query_cache = query_cache
        self._preprocessor = query_preprocessor
        self._session_history: dict[str, list[str]] = {}  # session_key → recent skill names

        # Determine cache path
        if workspace_skills_dir is None:
            workspace_skills_dir = skills_loader.workspace_skills
        cache_path = workspace_skills_dir / ".vector_cache.json"

        self._skill_index = SkillVectorIndex(embedding, cache_path)
        self._index_built = False

    def ensure_index(self) -> None:
        """Build the vector index if not already built. Called lazily on first route()."""
        if self._index_built and self._skill_index.size > 0:
            return
        skills = self._loader.list_skills(filter_unavailable=False)
        if skills:
            self._skill_index.build(skills, self._loader)
            self._index_built = True

    def route(
        self,
        user_message: str,
        session_key: str = "",
        channel: str = "",
    ) -> SkillRoutingResult:
        """Run the 4-layer routing pipeline.

        Args:
            user_message: Raw user input text.
            session_key: Session identifier for history tracking.
            channel: Channel name (cli, telegram, etc.).

        Returns:
            SkillRoutingResult with candidates for LLM decision.
        """
        self.ensure_index()

        # Determine always-on skills (bypass all layers)
        always_skills = self._loader.get_always_skills()
        always_set = set(always_skills)

        # Get available skills (filtered by requirements, activate_when)
        all_skills = self._loader.list_skills(filter_unavailable=True, include_stale=True)
        available = [s for s in all_skills if s["name"] not in always_set]
        available_names = [s["name"] for s in available]

        if not available:
            return SkillRoutingResult(
                always_skills=always_skills,
                layer1_matches=[],
                candidates=[],
                all_available=[],
            )

        # ---- Layer 1: Rule matching ----
        layer1_hits = self._layer1_rule_match(user_message, available)

        # ---- Layer 2: Semantic vector retrieval ----
        semantic_candidates = self._layer2_semantic_search(user_message)

        # ---- Layer 3: Context & behavior filtering ----
        filtered = self._layer3_filter(semantic_candidates, layer1_hits, available)

        # ---- Layer 4: Scoring & ranking ----
        candidates = self._layer4_score_and_rank(
            filtered, layer1_hits, session_key, available,
        )

        # Record session history
        if candidates and session_key:
            self._session_history.setdefault(session_key, []).append(candidates[0].name)
            # Keep only last 10
            self._session_history[session_key] = self._session_history[session_key][-10:]

        return SkillRoutingResult(
            always_skills=always_skills,
            layer1_matches=list(layer1_hits.keys()),
            candidates=candidates,
            all_available=available_names,
        )

    def invalidate(self, skill_name: str | None = None, action: str = "update") -> None:
        """Called when skills change. Rebuilds or updates the vector index."""
        if action == "delete" and skill_name:
            self._skill_index.remove(skill_name)
        elif skill_name and action in ("create", "patch", "update"):
            content = self._loader.load_skill(skill_name, inject_content=False)
            if content:
                self._skill_index.upsert(skill_name, content)
        else:
            # Full rebuild fallback
            self._index_built = False
            self.ensure_index()

    # -- Layer implementations ----------------------------------------------

    def _layer1_rule_match(
        self, user_message: str, available: list[dict[str, str]]
    ) -> dict[str, float]:
        """Layer 1: Keyword and slash-command matching.

        Returns dict of skill_name → match_score (1.0 for exact, 0.8 for keyword).
        """
        hits: dict[str, float] = {}
        msg_lower = user_message.lower().strip()

        for entry in available:
            name = entry["name"]

            # Slash command match: /skill-name
            if msg_lower.startswith(f"/{name}"):
                hits[name] = 1.0
                continue

            # Keyword matching from description
            desc = self._loader._get_skill_description(name)
            # Extract quoted trigger phrases
            triggers = re.findall(r'["""\']([^"""\']+)["""\']', desc)
            for trigger in triggers:
                if trigger.lower() in msg_lower:
                    hits[name] = max(hits.get(name, 0.0), 0.8)
                    break

        return hits

    def _layer2_semantic_search(self, user_message: str) -> list[dict]:
        """Layer 2: Vector similarity search. Returns list of {name, score}."""
        # Try to reuse cached query vector
        query_vec = None
        if self._query_cache:
            if self._preprocessor:
                processed = self._preprocessor.preprocess(user_message)
            else:
                processed = user_message
            query_vec = self._query_cache.get(processed)

        if query_vec is None:
            if self._preprocessor:
                processed = self._preprocessor.preprocess(user_message)
            else:
                processed = user_message
            query_vec = self._embedding.embed(processed)
            # Cache the query vector
            if self._query_cache and processed:
                self._query_cache.put(processed, query_vec)

        return self._skill_index.search(query_vec)

    def _layer3_filter(
        self,
        semantic_candidates: list[dict],
        layer1_hits: dict[str, float],
        available: list[dict[str, str]],
    ) -> list[dict]:
        """Layer 3: Filter out stale/inactive skills (unless Layer 1 matched).

        Returns filtered list of {name, score}.
        """
        available_set = {s["name"] for s in available}
        result = []

        for cand in semantic_candidates:
            name = cand["name"]

            # Must be in available set
            if name not in available_set:
                continue

            # Stale skills are kept only if Layer 1 matched them
            if name not in layer1_hits and self._tracker:
                qs = self._tracker.get_quality_score(name)
                if qs.is_stale:
                    continue

            result.append(cand)

        return result

    def _layer4_score_and_rank(
        self,
        filtered: list[dict],
        layer1_hits: dict[str, float],
        session_key: str,
        available: list[dict[str, str]],
    ) -> list[SkillCandidate]:
        """Layer 4: Compute composite scores and return top-3 candidates."""
        # Build lookup for descriptions
        desc_map = {s["name"]: self._loader._get_skill_description(s["name"]) for s in available}

        # Merge Layer 1 hits that didn't appear in semantic results
        semantic_names = {c["name"] for c in filtered}
        for name in layer1_hits:
            if name not in semantic_names and name in desc_map:
                filtered.append({"name": name, "score": 0.0})

        # Session recent skills
        session_recent = set(self._session_history.get(session_key, [])[-5:])

        candidates = []
        for cand in filtered:
            name = cand["name"]
            similarity = cand["score"]

            # Quality score
            quality_score = 0.0
            if self._tracker:
                qs = self._tracker.get_quality_score(name)
                quality_score = qs.quality_score

            # Composite score
            score = (
                _WEIGHT_SIMILARITY * similarity
                + _WEIGHT_RULE_MATCH * (1.0 if name in layer1_hits else 0.0)
                + _WEIGHT_QUALITY * quality_score
                + _WEIGHT_SESSION_RECENCY * (1.0 if name in session_recent else 0.0)
            )

            if score >= _MIN_SCORE:
                source = "combined" if name in layer1_hits and similarity > 0 else (
                    "rule" if name in layer1_hits else "semantic"
                )
                candidates.append(SkillCandidate(
                    name=name,
                    score=round(score, 4),
                    source=source,
                    similarity=round(similarity, 4),
                    quality_score=round(quality_score, 4),
                    description=desc_map.get(name, name),
                ))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:_MAX_CANDIDATES]
