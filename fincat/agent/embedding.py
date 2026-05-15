"""Embedding engine using bge-small-zh-v1.5 for semantic vector generation.

Backend priority:
1. FastEmbed (ONNX Runtime) — lightweight, no PyTorch dependency
2. sentence-transformers (PyTorch) — fallback if FastEmbed unavailable
"""

from __future__ import annotations

import hashlib
import os
import socket

import numpy as np

_HF_MIRROR = "https://hf-mirror.com"


def _ensure_hf_endpoint() -> None:
    """Set HF_ENDPOINT to mirror if HuggingFace is unreachable (e.g. mainland China)."""
    if os.environ.get("HF_ENDPOINT"):
        return
    try:
        socket.create_connection(("huggingface.co", 443), timeout=3).close()
    except (socket.timeout, OSError):
        os.environ["HF_ENDPOINT"] = _HF_MIRROR


_ensure_hf_endpoint()

_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_DIMENSION = 512


class EmbeddingEngine:
    """Local embedding via bge-small-zh-v1.5 (512-dim).

    Tries FastEmbed first (ONNX), falls back to sentence-transformers (PyTorch).
    Both produce identical 512-dim vectors for the same model.
    """

    def __init__(self, model_name: str = _MODEL_NAME, device: str = "cpu"):
        self._dimension = _DIMENSION
        self._backend = None

        # Try FastEmbed (ONNX Runtime) first
        try:
            from fastembed import TextEmbedding
            self._fastembed_model = TextEmbedding(model_name)
            self._backend = "fastembed"
            return
        except Exception:
            self._fastembed_model = None

        # Fallback to sentence-transformers (PyTorch)
        try:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(model_name, device=device)
            self._backend = "sentence-transformers"
            return
        except Exception:
            self._st_model = None

        raise RuntimeError(
            f"EmbeddingEngine: neither FastEmbed nor sentence-transformers available. "
            f"Install fastembed (pip install fastembed) or sentence-transformers."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def backend(self) -> str:
        return self._backend

    def embed(self, text: str) -> list[float]:
        """Single text → 512-dim vector."""
        if self._backend == "fastembed":
            vecs = list(self._fastembed_model.embed([text]))
            return vecs[0].tolist()
        else:
            vec = self._st_model.encode(text, normalize_embeddings=True)
            return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch texts → list of vectors (3-5x faster than逐条)."""
        if self._backend == "fastembed":
            vecs = list(self._fastembed_model.embed(texts))
            return [v.tolist() for v in vecs]
        else:
            vecs = self._st_model.encode(texts, normalize_embeddings=True, batch_size=64)
            return vecs.tolist()

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        a_arr = np.asarray(a, dtype=np.float32)
        b_arr = np.asarray(b, dtype=np.float32)
        return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr) + 1e-10))

    @staticmethod
    def content_hash(text: str) -> str:
        """SHA-256 hash for dedup / integrity checks."""
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
