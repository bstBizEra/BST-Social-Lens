-- SLL-DATA-HK-001 §10 — Housekeeper state (v0.1). Additive; append/supersede; no evidence lives here.
-- Findings are Housekeeper STATE, not market facts: `last_seen_run_id`/`status` may be updated in place
-- (documented R8 exception — the table is not a decisions/links table); resolution supersedes, never deletes.
CREATE SCHEMA IF NOT EXISTS housekeeping;

CREATE TABLE IF NOT EXISTS housekeeping.runs (
    run_id          BIGSERIAL PRIMARY KEY,
    trigger         TEXT NOT NULL CHECK (trigger IN ('scheduled','admin','startup','test')),
    hk_version      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    dry_run         BOOLEAN NOT NULL DEFAULT false,
    checks_run      INTEGER NOT NULL DEFAULT 0,
    findings_open   INTEGER NOT NULL DEFAULT 0,
    actions_taken   INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    error           TEXT
);

-- Latest watermark per stage; history is the run that wrote it (§6).
CREATE TABLE IF NOT EXISTS housekeeping.watermarks (
    stage               TEXT PRIMARY KEY CHECK (stage IN ('capture','raw','ingest','extract','geo','resolve','snapshot','publish','retention')),
    run_id              BIGINT NOT NULL REFERENCES housekeeping.runs (run_id),
    enabled             BOOLEAN NOT NULL DEFAULT true,
    last_success_at     TIMESTAMPTZ,
    last_failure_at     TIMESTAMPTZ,
    records_in          BIGINT,
    records_pending     BIGINT,
    oldest_pending_at   TIMESTAMPTZ,
    lag_seconds         BIGINT,
    method_version      TEXT,
    behind_version      BIGINT,
    detail              JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per reconciliation check per run (§7).
CREATE TABLE IF NOT EXISTS housekeeping.checks (
    check_id        BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES housekeeping.runs (run_id),
    check_code      TEXT NOT NULL CHECK (check_code IN ('R-EVID','R-SIGHT','R-EXTR','R-GEO','R-RES','R-PROP','R-SNAP','R-PUB','R-RET')),
    expected        BIGINT,
    observed        BIGINT,
    ratio           REAL CHECK (ratio IS NULL OR (ratio >= 0 AND ratio <= 1)),
    health          TEXT NOT NULL CHECK (health IN ('healthy','degraded','failing','unknown','disabled','error')),
    duration_ms     INTEGER,
    note            TEXT,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hk_checks_run ON housekeeping.checks (run_id);

-- Findings (§8): identity = (finding_type, entity_type, entity_id); NULL entity_id = aggregate.
CREATE TABLE IF NOT EXISTS housekeeping.findings (
    finding_id          BIGSERIAL PRIMARY KEY,
    finding_type        TEXT NOT NULL CHECK (finding_type IN (
                            'RAW_MISSING','RAW_ORPHAN','RECORD_WITHOUT_EVENT','QUARANTINED_PAYLOAD','EXTRACTION_STALE','EXTRACTION_MISSING',
                            'PARSER_REGRESSION','SCHEMA_DRIFT','GEO_UNRESOLVED','GEO_CONFLICT','MATCH_MISSING','MATCH_AMBIGUOUS','PROPERTY_EMPTY',
                            'PROPERTY_STALE','SNAPSHOT_STALE','DQ_REGRESSION','PUBLICATION_INELIGIBLE','RETENTION_OVERDUE','PROTECTED_PURGE_ATTEMPT',
                            'LINEAGE_BROKEN','SYNC_LAG','HK_STALLED')),
    severity            TEXT NOT NULL CHECK (severity IN ('INFO','WARN','ERROR','CRITICAL')),
    entity_type         TEXT NOT NULL DEFAULT 'aggregate',
    entity_id           TEXT,
    count               BIGINT NOT NULL DEFAULT 1,
    expected_state      TEXT,
    observed_state      TEXT,
    recommended_action  TEXT,
    auto_action_allowed BOOLEAN NOT NULL DEFAULT false,
    status              TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACTIONED','REVIEW','RESOLVED','SUPPRESSED')),
    first_seen_run_id   BIGINT NOT NULL REFERENCES housekeeping.runs (run_id),
    last_seen_run_id    BIGINT NOT NULL REFERENCES housekeeping.runs (run_id),
    resolved_run_id     BIGINT REFERENCES housekeeping.runs (run_id),
    resolved_at         TIMESTAMPTZ,
    supersedes_finding_id BIGINT REFERENCES housekeeping.findings (finding_id),
    sample              JSONB NOT NULL DEFAULT '[]'::jsonb,        -- ≤ 20 entity ids for aggregates
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hk_findings_identity ON housekeeping.findings (finding_type, entity_type, COALESCE(entity_id, '')) WHERE status IN ('OPEN','ACTIONED','REVIEW');
CREATE INDEX IF NOT EXISTS idx_hk_findings_status ON housekeeping.findings (status, severity);

-- Actions (§9) — populated from v0.2 (Actor); table exists so audit references are stable.
CREATE TABLE IF NOT EXISTS housekeeping.actions (
    action_id       BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES housekeeping.runs (run_id),
    finding_id      BIGINT REFERENCES housekeeping.findings (finding_id),
    action_type     TEXT NOT NULL CHECK (action_type IN ('REEXTRACT','RECOMPUTE_SNAPSHOTS','PURGE_BY_POLICY','MARK_RAW_NEEDED','BACKFILL_EVENTS')),
    target_type     TEXT,
    target_id       TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    result          JSONB NOT NULL DEFAULT '{}'::jsonb,
    audit_event_id  BIGINT REFERENCES audit.events (event_id)
);

-- Quarantine (§8): rejected inputs, context only — never a body beyond the server cap, never record text.
CREATE TABLE IF NOT EXISTS housekeeping.quarantine (
    quarantine_id   BIGSERIAL PRIMARY KEY,
    reason          TEXT NOT NULL CHECK (reason IN ('HASH_MISMATCH','BODY_TOO_LARGE','JSON_INVALID','SCHEMA_UNKNOWN','PLATFORM_UNKNOWN','PARSER_FAILED','PRIVACY_POLICY','SECURITY_ANOMALY')),
    payload_hash    TEXT,
    platform        TEXT,
    page_url        TEXT,
    body_bytes      INTEGER,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    resolution      TEXT
);

-- Lifecycle state transitions (§10): append-only, superseding.
CREATE TABLE IF NOT EXISTS housekeeping.lifecycle_states (
    state_id            BIGSERIAL PRIMARY KEY,
    entity_type         TEXT NOT NULL CHECK (entity_type IN ('observation','market_property')),
    entity_id           TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('CURRENT','AGING','STALE','HISTORICAL','WITHDRAWN','SUPERSEDED')),
    since               TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason              TEXT,
    run_id              BIGINT REFERENCES housekeeping.runs (run_id),
    supersedes_state_id BIGINT REFERENCES housekeeping.lifecycle_states (state_id)
);
CREATE INDEX IF NOT EXISTS idx_hk_lifecycle_entity ON housekeeping.lifecycle_states (entity_type, entity_id, state_id DESC);

-- Raw evidence the server is missing for records it holds (§9 MARK_RAW_NEEDED). The extension reads GET /raw/needed on
-- sync and re-sends bodies it still holds; a row clears when the raw capture arrives. Hashes only.
CREATE TABLE IF NOT EXISTS housekeeping.raw_needed (
    payload_hash    TEXT PRIMARY KEY,
    run_id          BIGINT REFERENCES housekeeping.runs (run_id),
    asked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    served_count    INTEGER NOT NULL DEFAULT 0
);

