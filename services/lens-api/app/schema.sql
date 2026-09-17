-- BST Social Lens — LensDB schema (PostgreSQL 15+)
-- Applied idempotently by db.init_db() on startup.

CREATE TABLE IF NOT EXISTS records (
    key               TEXT PRIMARY KEY,           -- `${platform}:${post_id}`
    platform          TEXT NOT NULL,
    post_id           TEXT NOT NULL,
    record_type       TEXT NOT NULL DEFAULT 'post',  -- post | comment
    parent_post_id    TEXT,                          -- set on comments
    permalink         TEXT,
    container_id      TEXT,                        -- group id / hashtag / search term
    container_name    TEXT,
    container_type    TEXT,                        -- group | page | profile | feed | hashtag | search
    matched_keywords  TEXT[] NOT NULL DEFAULT '{}',
    match_score       INTEGER NOT NULL DEFAULT 0,
    matched_via       TEXT,                        -- post | comment | author
    url_hash          TEXT,                        -- sha256(normalized permalink)
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
CREATE INDEX IF NOT EXISTS idx_records_type          ON records (record_type);
CREATE INDEX IF NOT EXISTS idx_records_parent        ON records (parent_post_id);
CREATE INDEX IF NOT EXISTS idx_records_match_score   ON records (match_score);
CREATE INDEX IF NOT EXISTS idx_records_matched_gin   ON records USING gin (matched_keywords);
CREATE INDEX IF NOT EXISTS idx_records_url_hash      ON records (url_hash);

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

-- Seen-link frontier: prevents re-opening the same permalink/link across machines.
CREATE TABLE IF NOT EXISTS seen_links (
    url_hash      TEXT PRIMARY KEY,   -- sha256(normalized url)
    url           TEXT NOT NULL,
    platform      TEXT,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_status   TEXT NOT NULL DEFAULT 'seen',  -- seen | queued | fetched | failed | skipped
    fetch_count   INTEGER NOT NULL DEFAULT 0,
    refresh_after TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_seen_updated ON seen_links (updated_at DESC);

-- ---------------------------------------------------------------------------
-- Phase 5 / SLL-PROP-DATA-001B — L0 RAW on the server + provenance (additive)
-- ---------------------------------------------------------------------------

-- L0: one row per distinct payload body (SHA-256). Body may be purged by retention;
-- the row (hash + capture context) is never deleted, so provenance survives the body.
CREATE TABLE IF NOT EXISTS raw_captures (
    payload_hash    TEXT PRIMARY KEY,               -- sha256(body) computed in the extension
    platform        TEXT,
    url             TEXT NOT NULL,                  -- request URL that produced the payload
    method          TEXT,
    status          INTEGER,
    source          TEXT,                           -- fetch | xhr | embedded | dom
    page_url        TEXT,
    captured_at     TIMESTAMPTZ NOT NULL,           -- first capture
    body            TEXT,                           -- NULL after body retention
    body_bytes      INTEGER NOT NULL DEFAULT 0,
    truncated       BOOLEAN NOT NULL DEFAULT false,
    parser_version  TEXT,
    ext_version     TEXT,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    body_purged_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_raw_captured_at ON raw_captures (captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_platform    ON raw_captures (platform);

-- Capture events: every time a source record was seen, and from which payload.
-- A post captured twice keeps both events (I1); the record row is one.
CREATE TABLE IF NOT EXISTS capture_events (
    id              BIGSERIAL PRIMARY KEY,
    record_key      TEXT NOT NULL,                  -- `${platform}:${post_id}` (no FK: events may arrive before/after rows)
    payload_hash    TEXT,                           -- → raw_captures.payload_hash (may arrive later)
    captured_at     TIMESTAMPTZ NOT NULL,
    page_url        TEXT,
    parser_version  TEXT,
    ingest_source   TEXT,
    UNIQUE (record_key, payload_hash, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_capev_record ON capture_events (record_key);
CREATE INDEX IF NOT EXISTS idx_capev_hash   ON capture_events (payload_hash);

-- L1 provenance columns
ALTER TABLE records ADD COLUMN IF NOT EXISTS content_hash        TEXT;     -- sha256 of normalised content fields (extension)
ALTER TABLE records ADD COLUMN IF NOT EXISTS first_payload_hash  TEXT;     -- payload that first produced this row
ALTER TABLE records ADD COLUMN IF NOT EXISTS last_payload_hash   TEXT;     -- most recent payload
ALTER TABLE records ADD COLUMN IF NOT EXISTS capture_count       INTEGER NOT NULL DEFAULT 1;
ALTER TABLE records ADD COLUMN IF NOT EXISTS protected           BOOLEAN NOT NULL DEFAULT false;  -- referenced by a published dataset → never purged (D3)
-- 0.7.2 sightings: the context a capture event happened in (container:<group id> | page:<host/path>); NULL for older extensions
ALTER TABLE capture_events ADD COLUMN IF NOT EXISTS context TEXT;
CREATE INDEX IF NOT EXISTS idx_capev_context ON capture_events (record_key, context);
