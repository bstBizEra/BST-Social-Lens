-- BST Social Lens — L2 remainder + L3 shell (SLL-PROP-DATA-001F draft; physical form of 001E and the
-- 001G/001H hooks). *** DRAFT — NOT APPLIED *** : this file is intentionally absent from db.L2_SCHEMA_PATHS
-- until 001C–001E freeze (ADR-0005 §2.4). It is linted in CI like every other schema file.
--
-- Schemas: resolution (clusters, candidates, decisions, merges) · market (properties, statistics snapshots)
--          · advertiser (advertisers, author links, contacts) · publish (datasets, dataset versions, members)
-- Rules enforced here: append-only decisions with `supersedes`; no bare `price` anywhere; statistics only as
-- versioned snapshots; MP ids never deleted (status, not DELETE); contacts restricted; publish is the only L3 path.

CREATE SCHEMA IF NOT EXISTS resolution;
CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS advertiser;
CREATE SCHEMA IF NOT EXISTS publish;

-- ============================================================================ resolution
CREATE TABLE IF NOT EXISTS resolution.runs (
    run_id              BIGSERIAL PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    trigger             TEXT NOT NULL CHECK (trigger IN ('scheduled','admin','backfill','test')),
    permalink_rules_version TEXT NOT NULL,
    cluster_version     TEXT NOT NULL,
    blocking_version    TEXT NOT NULL,
    match_version       TEXT NOT NULL,
    records_in          INTEGER NOT NULL DEFAULT 0,
    clusters_out        INTEGER NOT NULL DEFAULT 0,
    candidates_out      INTEGER NOT NULL DEFAULT 0,
    decisions_out       INTEGER NOT NULL DEFAULT 0,
    error               TEXT
);

-- Listing clusters (001E §4): append-only; a split creates new ids and supersedes the old one.
CREATE TABLE IF NOT EXISTS resolution.listing_clusters (
    cluster_id          TEXT PRIMARY KEY,                       -- 'C-<min record_key>' (deterministic)
    cluster_version     TEXT NOT NULL,
    run_id              BIGINT NOT NULL REFERENCES resolution.runs (run_id),
    status              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED')),
    superseded_by       TEXT REFERENCES resolution.listing_clusters (cluster_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS resolution.cluster_members (
    cluster_id          TEXT NOT NULL REFERENCES resolution.listing_clusters (cluster_id),
    record_key          TEXT NOT NULL,                          -- L1 key; no FK by design (I1 / retention independence)
    PRIMARY KEY (cluster_id, record_key)
);
CREATE INDEX IF NOT EXISTS idx_cluster_members_record ON resolution.cluster_members (record_key);
CREATE TABLE IF NOT EXISTS resolution.cluster_edges (
    edge_id             BIGSERIAL PRIMARY KEY,
    cluster_id          TEXT NOT NULL REFERENCES resolution.listing_clusters (cluster_id),
    record_a            TEXT NOT NULL,
    record_b            TEXT NOT NULL,
    rule                TEXT NOT NULL CHECK (rule IN ('exact','two_strong','strong_plus_weak')),
    signals             JSONB NOT NULL DEFAULT '{}'::jsonb,     -- name → evidence
    CHECK (record_a < record_b)
);

-- Market properties (001E §7): ids allocated once; status instead of DELETE (E5).
CREATE TABLE IF NOT EXISTS market.properties (
    market_property_id  TEXT PRIMARY KEY CHECK (market_property_id ~ '^MP-[0-9A-HJKMNP-TV-Z]{26}$'),  -- ULID
    status              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED','SPLIT')),
    superseded_by       TEXT REFERENCES market.properties (market_property_id),
    created_from_observation_id BIGINT NOT NULL REFERENCES extract.observations (observation_id),
    created_by_run_id   BIGINT REFERENCES resolution.runs (run_id),
    asset_type          TEXT NOT NULL CHECK (asset_type IN ('LAND','HOUSE','APARTMENT','COMMERCIAL','WAREHOUSE','HOTEL','FARM','DEVELOPMENT_LAND','BUILDING','OTHER','UNKNOWN')),
    primary_resolved_location_id BIGINT REFERENCES geo.resolved_locations (resolved_location_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((status = 'SUPERSEDED') = (superseded_by IS NOT NULL))
);

-- Candidates (append-only): what the matcher saw, with every signal (001E §11.2 explainability).
CREATE TABLE IF NOT EXISTS resolution.entity_candidates (
    candidate_id        BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES resolution.runs (run_id),
    observation_id      BIGINT NOT NULL REFERENCES extract.observations (observation_id),
    peer_observation_id BIGINT REFERENCES extract.observations (observation_id),
    market_property_id  TEXT REFERENCES market.properties (market_property_id),
    score               SMALLINT NOT NULL CHECK (score >= 0 AND score <= 100),
    proposed_decision   TEXT NOT NULL CHECK (proposed_decision IN ('HIGH_CONFIDENCE_MATCH','REVIEW_REQUIRED','SEPARATE_CANDIDATE')),
    forced_rule         TEXT,
    signals             JSONB NOT NULL DEFAULT '[]'::jsonb,     -- [{name, contribution, evidence}]
    blocking_reasons    JSONB NOT NULL DEFAULT '[]'::jsonb,
    match_version       TEXT NOT NULL,
    blocking_version    TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (peer_observation_id IS NOT NULL OR market_property_id IS NOT NULL),
    UNIQUE (observation_id, peer_observation_id, market_property_id, match_version)
);
CREATE INDEX IF NOT EXISTS idx_candidates_obs ON resolution.entity_candidates (observation_id);

-- Decisions (append-only, I8): current = latest non-superseded; HUMAN rows always win over machine rows.
CREATE TABLE IF NOT EXISTS resolution.entity_decisions (
    decision_id         BIGSERIAL PRIMARY KEY,
    observation_id      BIGINT NOT NULL REFERENCES extract.observations (observation_id),
    market_property_id  TEXT NOT NULL REFERENCES market.properties (market_property_id),
    decision            TEXT NOT NULL CHECK (decision IN ('HIGH_CONFIDENCE_MATCH','REVIEW_REQUIRED','SEPARATE_CANDIDATE','CONFIRMED','REJECTED','UNLINKED')),
    source              TEXT NOT NULL CHECK (source ~ '^(MATCH_V[0-9.]+(-[a-z]+)?|HUMAN)$'),
    reviewer            TEXT,
    candidate_id        BIGINT REFERENCES resolution.entity_candidates (candidate_id),
    supersedes_decision_id BIGINT REFERENCES resolution.entity_decisions (decision_id),
    run_id              BIGINT REFERENCES resolution.runs (run_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((source = 'HUMAN') = (reviewer IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_decisions_obs ON resolution.entity_decisions (observation_id, decision_id DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_mp  ON resolution.entity_decisions (market_property_id);

-- Current decision per observation: the latest row that no other row supersedes.
CREATE OR REPLACE VIEW resolution.current_decisions AS
SELECT d.*
FROM resolution.entity_decisions d
WHERE NOT EXISTS (SELECT 1 FROM resolution.entity_decisions s WHERE s.supersedes_decision_id = d.decision_id)
  AND d.decision_id = (SELECT max(decision_id) FROM resolution.entity_decisions x
                       WHERE x.observation_id = d.observation_id
                         AND NOT EXISTS (SELECT 1 FROM resolution.entity_decisions y WHERE y.supersedes_decision_id = x.decision_id));

-- Merge / split audit (one row per transition; the decisions it produced reference it by run/audit id).
CREATE TABLE IF NOT EXISTS resolution.property_transitions (
    transition_id       BIGSERIAL PRIMARY KEY,
    kind                TEXT NOT NULL CHECK (kind IN ('MERGE','SPLIT','UNLINK')),
    source_property_id  TEXT NOT NULL REFERENCES market.properties (market_property_id),
    target_property_ids TEXT[] NOT NULL,
    actor               TEXT NOT NULL,                          -- reviewer id or 'MATCH_Vx'
    reason              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================ market statistics (snapshots only)
-- Derived, never "the price" (I4/I7): a new row per recomputation; readers take the latest per property.
CREATE TABLE IF NOT EXISTS market.property_stats_snapshots (
    snapshot_id         BIGSERIAL PRIMARY KEY,
    market_property_id  TEXT NOT NULL REFERENCES market.properties (market_property_id),
    stats_version       TEXT NOT NULL,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    observation_count   INTEGER NOT NULL,
    advertiser_count    INTEGER NOT NULL,
    cluster_count       INTEGER NOT NULL,
    first_observed      TIMESTAMPTZ,
    last_observed       TIMESTAMPTZ,
    asking_stats        JSONB NOT NULL DEFAULT '{}'::jsonb,     -- {price_type: {min_lak,max_lak,median_lak,latest_lak,dispersion,per_sqm_median_lak,n}}
    resolution_confidence REAL NOT NULL CHECK (resolution_confidence >= 0 AND resolution_confidence <= 100),
    review_state        TEXT NOT NULL CHECK (review_state IN ('CLEAN','PENDING_REVIEW','DISPUTED')),
    dq_grade            TEXT CHECK (dq_grade IN ('A','B','C','D')),   -- 001G
    UNIQUE (market_property_id, stats_version, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_stats_latest ON market.property_stats_snapshots (market_property_id, computed_at DESC);

-- ============================================================================ advertiser
CREATE TABLE IF NOT EXISTS advertiser.advertisers (
    advertiser_id       TEXT PRIMARY KEY CHECK (advertiser_id ~ '^AD-[0-9A-HJKMNP-TV-Z]{26}$'),
    role                TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (role IN ('OWNER_CLAIMED','FREELANCE_AGENT','COMPANY_AGENT','DEVELOPER','COMPANY','UNKNOWN')),
    display_name        TEXT,                                   -- as already present in L1; no enrichment (I10)
    status              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED')),
    superseded_by       TEXT REFERENCES advertiser.advertisers (advertiser_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS advertiser.author_links (
    link_id             BIGSERIAL PRIMARY KEY,
    advertiser_id       TEXT NOT NULL REFERENCES advertiser.advertisers (advertiser_id),
    author_hash         TEXT NOT NULL,                          -- L1 author_hash; no FK
    platform            TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('CONTACT_HASH','HUMAN')),
    reviewer            TEXT,
    supersedes_link_id  BIGINT REFERENCES advertiser.author_links (link_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((source = 'HUMAN') = (reviewer IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_author_links_hash ON advertiser.author_links (author_hash);
CREATE TABLE IF NOT EXISTS advertiser.advertiser_contacts (
    advertiser_id       TEXT NOT NULL REFERENCES advertiser.advertisers (advertiser_id),
    contact_hash        TEXT NOT NULL REFERENCES extract.contact_points (contact_hash),   -- restricted (D5); never published
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (advertiser_id, contact_hash)
);

-- ============================================================================ publish (L3 shell — 001G/001H)
CREATE TABLE IF NOT EXISTS publish.datasets (
    dataset_id          TEXT PRIMARY KEY,                       -- e.g. 'lao-residential-market'
    description         TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS publish.dataset_versions (
    dataset_id          TEXT NOT NULL REFERENCES publish.datasets (dataset_id),
    version             TEXT NOT NULL,                          -- semver or date tag
    status              TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','PUBLISHED','WITHDRAWN')),
    published_at        TIMESTAMPTZ,
    published_by        TEXT,
    admin_version       TEXT REFERENCES geo.admin_versions (admin_version),
    stats_version       TEXT,
    checksum            TEXT,                                   -- of the immutable export
    note                TEXT,
    PRIMARY KEY (dataset_id, version),
    CHECK ((status = 'PUBLISHED') = (published_at IS NOT NULL))
);
-- Membership is immutable per version: which market properties and which observations were included.
CREATE TABLE IF NOT EXISTS publish.version_members (
    dataset_id          TEXT NOT NULL,
    version             TEXT NOT NULL,
    market_property_id  TEXT NOT NULL REFERENCES market.properties (market_property_id),
    snapshot_id         BIGINT NOT NULL REFERENCES market.property_stats_snapshots (snapshot_id),
    observation_ids     BIGINT[] NOT NULL,
    PRIMARY KEY (dataset_id, version, market_property_id),
    FOREIGN KEY (dataset_id, version) REFERENCES publish.dataset_versions (dataset_id, version)
);
-- D3 hook: the publish step sets records.protected = true for every L1 row behind a published member
-- (that UPDATE lives in the publish service, not here — this file never touches L1).
