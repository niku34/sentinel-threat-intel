"""pgvector-backed semantic cache for Veriff verdicts.

Claims are embedded with all-MiniLM-L6-v2 (384-dim) and stored in the
`claim_embeddings` table. On a new claim we run an approximate nearest
neighbour search (cosine distance via the pgvector `<=>` operator); if the
closest cached claim is above the similarity threshold we can reuse its
verdict instead of re-running the seven scrapers — turning a multi-second
fact-check into a millisecond lookup.

Design notes:
- All DB / model failures fail *open* (return None / no-op) so the verdict
  pipeline always resolves even if pgvector or the model is unavailable.
- Embeddings are unit-normalised, so cosine similarity == 1 - cosine distance.
- `store()` only persists reasonably confident verdicts (> 0.6) to avoid
  poisoning the cache with low-signal UNVERIFIED noise.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import List, Optional

from .embeddings import EMBED_DIM, get_embeddings_via_server

logger = logging.getLogger("veriff.semantic_cache")

DEFAULT_MATCH_THRESHOLD = 0.92
MIN_STORE_CONFIDENCE = 0.6

# A cached verdict is only reusable for this long. Enforced in `get_cached()`
# itself, not just in `cleanup_old()` — nothing schedules cleanup, so relying on
# it to bound staleness meant a verdict could be served indefinitely. The age is
# measured from `created_at` (when the verdict was computed), never `last_used`:
# `get_cached` bumps `last_used` on every hit, so a last_used-based filter would
# keep a stale row alive forever precisely because it kept being served.
MAX_CACHE_AGE_DAYS = 7

_SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS claim_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_text TEXT NOT NULL,
    claim_hash VARCHAR(64) UNIQUE NOT NULL,
    embedding vector({EMBED_DIM}),
    verdict VARCHAR,
    confidence FLOAT,
    sources_json TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS claim_embeddings_vector_idx
    ON claim_embeddings USING ivfflat (embedding vector_cosine_ops);
"""


def _claim_hash(claim_text: str) -> str:
    return hashlib.sha256(claim_text.strip().lower().encode("utf-8")).hexdigest()


class SemanticCache:
    MODEL = "all-MiniLM-L6-v2"

    def __init__(self, db_url: str, ensure_schema: bool = True):
        self.db_url = db_url
        self._available = False
        self._conn = None
        try:
            import psycopg2
            from pgvector.psycopg2 import register_vector

            self._psycopg2 = psycopg2
            self._register_vector = register_vector
            self._conn = psycopg2.connect(db_url)
            self._conn.autocommit = True
            if ensure_schema:
                self._try_ensure_schema()
            # register_vector needs the `vector` type to already exist.
            register_vector(self._conn)
            # The cache is usable as long as the table exists — even if this
            # (unprivileged) role couldn't CREATE it. The migration
            # (009_claim_embeddings.sql) is the source of truth for the schema.
            self._available = self._table_exists()
            if not self._available:
                logger.warning(
                    "claim_embeddings table not found — run migration "
                    "009_claim_embeddings.sql; running without cache"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SemanticCache unavailable (%s) — running without cache", exc)
            self._available = False

    # ── internals ─────────────────────────────────────────────
    def _try_ensure_schema(self) -> None:
        """Best-effort schema creation. Ignored if the role lacks CREATE."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
        except Exception as exc:  # noqa: BLE001
            logger.info("schema auto-create skipped (%s) — relying on migration", exc)
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001
                pass

    def _table_exists(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.claim_embeddings')")
                return cur.fetchone()[0] is not None
        except Exception:  # noqa: BLE001
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return False

    @property
    def available(self) -> bool:
        return self._available

    def _reconnect(self) -> bool:
        """Best-effort reconnect after a dropped/aborted connection."""
        try:
            self._conn = self._psycopg2.connect(self.db_url)
            self._conn.autocommit = True
            self._register_vector(self._conn)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("SemanticCache reconnect failed: %s", exc)
            self._available = False
            return False

    # ── public API ────────────────────────────────────────────
    def get_cached(
        self,
        claim_text: str,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        max_age_days: int = MAX_CACHE_AGE_DAYS,
    ) -> Optional[dict]:
        """Return the closest cached verdict above `threshold`, else None.

        Entries whose verdict was computed more than `max_age_days` ago are
        never returned, regardless of whether `cleanup_old()` has ever run.
        """
        if not self._available or not claim_text:
            return None
        vecs = get_embeddings_via_server([claim_text])
        vec = vecs[0] if vecs else None
        if vec is None:
            return None
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, claim_text, verdict, confidence, sources_json,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM claim_embeddings
                    WHERE embedding IS NOT NULL
                      AND created_at > NOW() - make_interval(days => %s)
                      AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT 1
                    """,
                    (vec, int(max_age_days), vec, threshold, vec),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cache_id, cached_text, verdict, confidence, sources_json, similarity = row
                cur.execute(
                    "UPDATE claim_embeddings SET last_used = NOW() WHERE id = %s",
                    (cache_id,),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_cached query failed: %s", exc)
            if self._conn:
                try:
                    self._conn.rollback()
                except Exception:  # noqa: BLE001
                    self._reconnect()
            return None

        try:
            sources = json.loads(sources_json) if sources_json else []
        except (ValueError, TypeError):
            sources = []
        return {
            "verdict": verdict,
            "confidence": float(confidence) if confidence is not None else 0.0,
            "sources": sources,
            "from_cache": True,
            "similarity": round(float(similarity), 4),
            "cached_claim": cached_text,
        }

    def store(
        self,
        claim_text: str,
        verdict: str,
        confidence: float,
        sources: Optional[List[dict]] = None,
    ) -> bool:
        """Upsert a verdict into the cache (only if confidence > 0.6)."""
        if not self._available or not claim_text:
            return False
        if confidence is None or float(confidence) <= MIN_STORE_CONFIDENCE:
            return False
        vecs = get_embeddings_via_server([claim_text])
        vec = vecs[0] if vecs else None
        if vec is None:
            return False
        chash = _claim_hash(claim_text)
        sources_json = json.dumps(sources or [], ensure_ascii=False)
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO claim_embeddings
                        (claim_text, claim_hash, embedding, verdict, confidence, sources_json, last_used)
                    VALUES (%s, %s, %s::vector, %s, %s, %s, NOW())
                    ON CONFLICT (claim_hash) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        verdict = EXCLUDED.verdict,
                        confidence = EXCLUDED.confidence,
                        sources_json = EXCLUDED.sources_json,
                        -- This row now holds a freshly computed verdict, so its
                        -- age restarts. Without this, re-verifying a claim would
                        -- keep the original created_at and the entry could never
                        -- be served again once it crossed MAX_CACHE_AGE_DAYS.
                        created_at = NOW(),
                        last_used = NOW()
                    """,
                    (claim_text, chash, vec, verdict, float(confidence), sources_json),
                )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("store failed: %s", exc)
            if self._conn:
                try:
                    self._conn.rollback()
                except Exception:  # noqa: BLE001
                    self._reconnect()
            return False

    def get_similar(
        self, claim_text: str, threshold: float = 0.75, limit: int = 3
    ) -> list:
        """Return up to `limit` past claims with similarity above `threshold`."""
        if not self._available or not claim_text:
            return []
        vecs = get_embeddings_via_server([claim_text])
        vec = vecs[0] if vecs else None
        if vec is None:
            return []
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT claim_text, verdict, confidence, created_at,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM claim_embeddings
                    WHERE embedding IS NOT NULL
                      AND 1 - (embedding <=> %s::vector) > %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vec, vec, threshold, vec, limit),
                )
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_similar query failed: %s", exc)
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return []
        result = []
        for row in rows:
            claim_t, verdict, confidence, created_at, similarity = row
            result.append({
                "claim_text": claim_t,
                "verdict": verdict,
                "confidence": float(confidence) if confidence is not None else 0.0,
                "checked_at": created_at.isoformat() if created_at else None,
                "similarity": round(float(similarity), 4),
            })
        return result

    def cleanup_old(self, max_age_days: int = MAX_CACHE_AGE_DAYS) -> int:
        """Delete cache rows older than the TTL. Returns rows removed.

        Purely a space reclaim: `get_cached()` already refuses to serve these
        rows, so correctness no longer depends on this running.
        """
        if not self._available:
            return 0
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM claim_embeddings "
                    "WHERE created_at < NOW() - make_interval(days => %s)",
                    (int(max_age_days),),
                )
                return cur.rowcount or 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("cleanup_old failed: %s", exc)
            return 0

    def stats(self) -> dict:
        """Aggregate cache stats for the API stats endpoint."""
        if not self._available:
            return {"total_cached": 0, "cache_hits_today": 0, "avg_similarity": 0.0}
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM claim_embeddings")
                total = cur.fetchone()[0] or 0
                cur.execute(
                    "SELECT COUNT(*) FROM claim_embeddings "
                    "WHERE last_used::date = NOW()::date "
                    "AND last_used > created_at"
                )
                hits_today = cur.fetchone()[0] or 0
                cur.execute("SELECT AVG(confidence) FROM claim_embeddings")
                avg_conf = cur.fetchone()[0]
            return {
                "total_cached": int(total),
                "cache_hits_today": int(hits_today),
                "avg_similarity": round(float(avg_conf), 4) if avg_conf is not None else 0.0,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats failed: %s", exc)
            return {"total_cached": 0, "cache_hits_today": 0, "avg_similarity": 0.0}

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    import os

    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv

    load_dotenv()
    cache = SemanticCache(os.environ["DATABASE_URL"])
    print("available:", cache.available)
    cache.store("AI deepfake audio of Tamil anchor", "MEDIUM_CONFIDENCE_FAKE", 0.72,
                [{"source": "AltNews", "url": "x"}])
    hit = cache.get_cached("Deepfake voice of Tamil news anchor", threshold=0.6)
    print("hit:", json.dumps(hit, ensure_ascii=False, indent=2) if hit else None)
    print("stats:", cache.stats())
