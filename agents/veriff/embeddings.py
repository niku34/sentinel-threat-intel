"""Shared sentence-transformers embedding helper for Veriff.

Both the semantic cache (pgvector) and the semantic matcher embed short
strings with the same model. Loading a SentenceTransformer is expensive
(~10s cold), so we memoise a single process-wide instance and reuse it.

The model (all-MiniLM-L6-v2) is small, CPU-friendly, and emits 384-dim
unit-normalisable vectors — the exact width of the pgvector column.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("veriff.embeddings")

MODEL_NAME = "all-MiniLM-L6-v2"
EMBED_DIM = 384
EMBED_SERVER_URL = "http://127.0.0.1:11435"

_MODEL = None
_MODEL_LOCK = threading.Lock()

# Simple LRU-style in-process cache keyed by (texts...) tuple
_EMBED_CACHE: Dict[Tuple[str, ...], List[List[float]]] = {}
_EMBED_CACHE_MAX = 512


def get_model():
    """Return a memoised SentenceTransformer, or None if unavailable."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # noqa: BLE001
            logger.warning("sentence-transformers unavailable: %s", exc)
            return None
        try:
            _MODEL = SentenceTransformer(MODEL_NAME)
            logger.info("Loaded embedding model %s", MODEL_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load embedding model %s: %s", MODEL_NAME, exc)
            _MODEL = None
    return _MODEL


def embed(text: str) -> Optional[List[float]]:
    """Embed a single string to a 384-float list, or None on failure."""
    model = get_model()
    if model is None or not text:
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("embed failed: %s", exc)
        return None
    return [float(x) for x in vec]


def embed_many(texts: Sequence[str]) -> Optional[List[List[float]]]:
    """Batch-embed a list of strings (normalised), or None on failure."""
    model = get_model()
    if model is None or not texts:
        return None
    try:
        vecs = model.encode(list(texts), normalize_embeddings=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("embed_many failed: %s", exc)
        return None
    return [[float(x) for x in v] for v in vecs]


def get_embeddings_via_server(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed texts via the persistent embedding server (no 10s cold start).

    Tries POST http://127.0.0.1:11435/embed with a 2s timeout. If the server
    is not running, falls back to loading the model in-process. Results are
    cached in _EMBED_CACHE for the lifetime of the calling process.
    """
    if not texts:
        return []

    cache_key = tuple(texts)
    if cache_key in _EMBED_CACHE:
        return _EMBED_CACHE[cache_key]

    # Try the persistent server first
    try:
        payload = json.dumps({"texts": texts}).encode()
        req = urllib.request.Request(
            f"{EMBED_SERVER_URL}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            result = data.get("embeddings")
            if result and isinstance(result, list):
                if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
                    _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
                _EMBED_CACHE[cache_key] = result
                return result
    except (urllib.error.URLError, OSError, ValueError):
        logger.debug("Embedding server unreachable — falling back to in-process model")
    except Exception as exc:
        logger.warning("Embedding server error: %s — falling back", exc)

    # Fall back: load the model in this process (original behaviour)
    result = embed_many(texts)
    if result:
        if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
            _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
        _EMBED_CACHE[cache_key] = result
    return result


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two vectors (0.0 on degenerate input)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
