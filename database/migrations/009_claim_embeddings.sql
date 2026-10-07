-- 009_claim_embeddings.sql
-- Day 13: Veriff semantic upgrade — pgvector claim cache.
--
-- Stores sentence-transformers (all-MiniLM-L6-v2, 384-dim) embeddings of
-- fact-checked claims so near-identical future claims can reuse a prior
-- verdict via approximate nearest-neighbour search instead of re-running
-- the seven-source scrape.
--
-- Requires the pgvector extension (PostgreSQL 18).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS claim_embeddings (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_text   TEXT NOT NULL,
    claim_hash   VARCHAR(64) UNIQUE NOT NULL,
    embedding    vector(384),
    verdict      VARCHAR,
    confidence   FLOAT,
    sources_json TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_used    TIMESTAMPTZ DEFAULT NOW()
);

-- Cosine-distance ANN index for the `embedding <=> query` lookups.
CREATE INDEX IF NOT EXISTS claim_embeddings_vector_idx
    ON claim_embeddings USING ivfflat (embedding vector_cosine_ops);

-- Supports the 7-day cleanup sweep.
CREATE INDEX IF NOT EXISTS claim_embeddings_last_used_idx
    ON claim_embeddings (last_used);

-- Grant to the application role (matches other Sentinel tables).
GRANT ALL PRIVILEGES ON claim_embeddings TO sentinel_app;
