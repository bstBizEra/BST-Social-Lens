-- BST Social Lens — LensDB schema (PostgreSQL 15+)
-- Applied idempotently by db.init_db() on startup.

CREATE TABLE IF NOT EXISTS records (
    key               TEXT PRIMARY KEY,           -- `${platform}:${post_id}`
    platform          TEXT NOT NULL,
    post_id           TEXT NOT NULL,
    permalink         TEXT,
    container_id      TEXT,                        -- group id / hashtag / search term
    container_name    TEXT,
    author_name       TEXT,
    author_id         TEXT,                        -- present only when hashing is off
    author_hash       TEXT,                        -- sha256(platform:author_id)
    author_url        TEXT,
    text              TEXT,
    lang              TEXT,
    created_at        TIMESTAMPTZ,
    captured_at       TIMESTAMPTZ NOT NULL,
    reactions_total   INTEGER,
    reactions_breakdown JSONB,
    comments_count    INTEGER,
    shares_count      INTEGER,
    views_count       INTEGER,
    media             JSONB NOT NULL DEFAULT '[]'::jsonb,
    hashtags          TEXT[] NOT NULL DEFAULT '{}',
    parser_version    TEXT,
    -- server bookkeeping
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingest_source     TEXT,
    ingest_version    TEXT
);

CREATE INDEX IF NOT EXISTS idx_records_platform      ON records (platform);
CREATE INDEX IF NOT EXISTS idx_records_container     ON records (container_id);
CREATE INDEX IF NOT EXISTS idx_records_created_at    ON records (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_records_captured_at   ON records (captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_records_hashtags_gin  ON records USING gin (hashtags);

-- Full-text-ish search on post text (simple config; swap for a Lao-aware config later).
CREATE INDEX IF NOT EXISTS idx_records_text_trgm ON records USING gin (text gin_trgm_ops);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id            BIGSERIAL PRIMARY KEY,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    source        TEXT,
    ext_version   TEXT,
    records_sent  INTEGER NOT NULL,
    inserted      INTEGER NOT NULL,
    updated       INTEGER NOT NULL,
    remote_addr   TEXT
);

-- Optional raw archive (extension can push raw payloads later; retained N days).
CREATE TABLE IF NOT EXISTS raw_payloads (
    id           BIGSERIAL PRIMARY KEY,
    platform     TEXT,
    url          TEXT,
    page_url     TEXT,
    source       TEXT,
    status       INTEGER,
    captured_at  TIMESTAMPTZ NOT NULL,
    body         TEXT,
    parsed_count INTEGER,
    parse_error  TEXT,
    stored_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_raw_captured_at ON raw_payloads (captured_at DESC);
