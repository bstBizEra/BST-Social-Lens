-- BST Social Lens — L2 INTELLIGENCE, extraction subset (SLL-PROP-DATA-001C; 001F partial DDL)
-- Applied idempotently by db.init_db() after schema.sql. ADDITIVE ONLY: this file must never
-- reference an L0/L1 table (records, raw_captures, capture_events, seen_links, ingest_runs,
-- raw_payloads) in a DDL/DML statement — scripts/schema_lint.py enforces it in CI.
--
-- Vocabulary rules (001A I2/I5, 001C §10.3): no bare `price`, `owner`, `property`, `area`,
-- `parcel`, `title` columns; claims are claims (method + confidence + span + review status
-- are NOT NULL), price observations are ASKING/WANTED only, contact raw values are
-- encrypted and live apart from the masked form (D5, C4).

CREATE SCHEMA IF NOT EXISTS extract;

-- One row per extraction invocation (audit anchor; I8-style explainability).
CREATE TABLE IF NOT EXISTS extract.runs (
    run_id                  BIGSERIAL PRIMARY KEY,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at             TIMESTAMPTZ,
    trigger                 TEXT NOT NULL CHECK (trigger IN ('scheduled', 'admin', 'backfill', 'test')),
    method_set              TEXT NOT NULL,                 -- e.g. 'RULE_V1' or 'RULE_V1+LLM_OLLAMA:qwen2.5:7b'
    method_version          TEXT NOT NULL,                 -- semver of the rules module
    keyword_groups_version  TEXT NOT NULL,
    records_in              INTEGER NOT NULL DEFAULT 0,
    observations_out        INTEGER NOT NULL DEFAULT 0,
    claims_out              INTEGER NOT NULL DEFAULT 0,
    retired                 BOOLEAN NOT NULL DEFAULT false, -- a retired run's observations are no longer "current"
    error                   TEXT
);

-- One observation per (source record, run). Never updated; re-runs append (001C §2).
CREATE TABLE IF NOT EXISTS extract.observations (
    observation_id          BIGSERIAL PRIMARY KEY,
    record_key              TEXT NOT NULL,                 -- `${platform}:${post_id}`; no FK to L1 by design (L1 retention may outlive nothing here — I1)
    run_id                  BIGINT NOT NULL REFERENCES extract.runs (run_id),
    content_hash            TEXT,                          -- L1 content_hash the run saw; a new hash ⇒ a new run is due
    record_type             TEXT NOT NULL DEFAULT 'post',
    signal_class            TEXT NOT NULL CHECK (signal_class IN (
                                'PROPERTY_SALE','PROPERTY_RENT','PROPERTY_WANTED','AGENT_ADVERTISEMENT','DEVELOPER_PROJECT',
                                'PRICE_DISCUSSION','MARKET_INFORMATION','NON_PROPERTY','UNCERTAIN')),
    signal_confidence       REAL NOT NULL CHECK (signal_confidence >= 0 AND signal_confidence <= 1),
    asset_type              TEXT NOT NULL CHECK (asset_type IN (
                                'LAND','HOUSE','APARTMENT','COMMERCIAL','WAREHOUSE','HOTEL','FARM','DEVELOPMENT_LAND',
                                'BUILDING','OTHER','UNKNOWN')),
    asset_confidence        REAL NOT NULL CHECK (asset_confidence >= 0 AND asset_confidence <= 1),
    extraction_method       TEXT NOT NULL,
    method_version          TEXT NOT NULL,
    keyword_groups_version  TEXT NOT NULL,
    observed_at             TIMESTAMPTZ NOT NULL,          -- = L1 captured_at at run time
    post_date               TIMESTAMPTZ,                   -- = L1 created_at
    signals                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    group_hits              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (record_key, run_id)
);
CREATE INDEX IF NOT EXISTS idx_obs_record   ON extract.observations (record_key, run_id DESC);
CREATE INDEX IF NOT EXISTS idx_obs_class    ON extract.observations (signal_class);
CREATE INDEX IF NOT EXISTS idx_obs_asset    ON extract.observations (asset_type);
CREATE INDEX IF NOT EXISTS idx_obs_observed ON extract.observations (observed_at DESC);

-- A claim is never a fact (I2): method, version, confidence, evidence span and review status are mandatory.
CREATE TABLE IF NOT EXISTS extract.claims (
    claim_id                BIGSERIAL PRIMARY KEY,
    observation_id          BIGINT NOT NULL REFERENCES extract.observations (observation_id),
    field                   TEXT NOT NULL CHECK (field IN (
                                'PRICE','AREA','FRONTAGE','DEPTH','TRANSACTION_TYPE','ASSET_TYPE','ADVERTISER_ROLE',
                                'LOCATION_TEXT','MAP_URL','COORDINATE','CONTACT','LAND_TITLE_MENTION','ROAD_ACCESS','PROJECT_NAME')),
    value_text              TEXT NOT NULL,
    span_start              INTEGER NOT NULL CHECK (span_start >= 0),   -- UTF-16 code units into NFC(text)
    span_end                INTEGER NOT NULL,
    extraction_method       TEXT NOT NULL,                 -- RULE_V1 | LLM_<provider>:<model> | HUMAN
    method_version          TEXT NOT NULL,
    confidence              REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    review_status           TEXT NOT NULL DEFAULT 'UNREVIEWED' CHECK (review_status IN ('UNREVIEWED','LOW_CONFIDENCE','CONFIRMED','CORRECTED','REJECTED')),
    normalised              JSONB NOT NULL DEFAULT '{}'::jsonb,   -- field-specific normalised columns (001C §6); CONTACT raw_value is NEVER stored here
    normalisation_status    TEXT NOT NULL DEFAULT 'OK' CHECK (normalisation_status IN ('OK','NO_FX','UNPARSED','NOT_APPLICABLE')),
    signals                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    supersedes_claim_id     BIGINT REFERENCES extract.claims (claim_id),  -- HUMAN corrections point at the claim they correct; the original stays
    reviewer                TEXT,                          -- set only when extraction_method = 'HUMAN'
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (span_end > span_start),
    CHECK (NOT (normalised ? 'raw_value'))
);
CREATE INDEX IF NOT EXISTS idx_claims_obs   ON extract.claims (observation_id);
CREATE INDEX IF NOT EXISTS idx_claims_field ON extract.claims (field);
CREATE INDEX IF NOT EXISTS idx_claims_review ON extract.claims (review_status) WHERE review_status IN ('LOW_CONFIDENCE','UNREVIEWED');

-- Time-series of asking/wanted prices (I4, I7). Append-only; no transaction prices exist here.
CREATE TABLE IF NOT EXISTS extract.price_observations (
    price_observation_id    BIGSERIAL PRIMARY KEY,
    observation_id          BIGINT NOT NULL REFERENCES extract.observations (observation_id),
    claim_id                BIGINT NOT NULL REFERENCES extract.claims (claim_id),
    price_type              TEXT NOT NULL CHECK (price_type IN (
                                'ASKING_SALE','ASKING_SALE_PER_SQM','ASKING_RENT_MONTHLY','ASKING_RENT_YEARLY','WANTED_BUDGET','UNCLASSIFIED_PRICE')),
    amount_original         NUMERIC(20,2) NOT NULL CHECK (amount_original >= 0),
    currency_original       TEXT NOT NULL CHECK (currency_original IN ('LAK','THB','USD','UNKNOWN')),
    price_basis             TEXT NOT NULL CHECK (price_basis IN ('TOTAL','PER_SQM','PER_MONTH','PER_YEAR','UNKNOWN')),
    amount_lak              NUMERIC(20,0),
    fx_rate                 NUMERIC(18,6),
    fx_rate_date            DATE,
    fx_source               TEXT CHECK (fx_source IN ('IDENTITY','BOL_REFERENCE','MANUAL')),
    price_per_sqm_lak       NUMERIC(20,0),
    observed_at             TIMESTAMPTZ NOT NULL,
    post_date               TIMESTAMPTZ,
    confidence              REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    CHECK ((amount_lak IS NULL) = (fx_rate IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_priceobs_obs  ON extract.price_observations (observation_id);
CREATE INDEX IF NOT EXISTS idx_priceobs_type ON extract.price_observations (price_type, observed_at DESC);

-- FX reference (C2): Bank of the Lao PDR reference rate, loaded manually/CSV first.
CREATE TABLE IF NOT EXISTS extract.fx_rates (
    currency                TEXT NOT NULL CHECK (currency IN ('THB','USD')),
    rate_date               DATE NOT NULL,
    lak_per_unit            NUMERIC(18,6) NOT NULL CHECK (lak_per_unit > 0),
    source                  TEXT NOT NULL CHECK (source IN ('BOL_REFERENCE','MANUAL')),
    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (currency, rate_date)
);

-- Contact points (D5, I10): hashed + masked; the raw value is encrypted with pgcrypto (C4)
-- using a key that lives only in the service environment, and is NULL when no key is set.
CREATE TABLE IF NOT EXISTS extract.contact_points (
    contact_hash            TEXT PRIMARY KEY,              -- sha256(kind:normalised:server_salt)
    kind                    TEXT NOT NULL CHECK (kind IN ('PHONE','LINE','WHATSAPP','FB_MESSENGER','OTHER')),
    masked_value            TEXT NOT NULL,
    raw_value_enc           BYTEA,                         -- pgp_sym_encrypt(raw, LENS_CONTACT_KEY); reviewer-only (001G)
    first_seen              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen               TIMESTAMPTZ NOT NULL DEFAULT now(),
    sightings               INTEGER NOT NULL DEFAULT 1
);

-- Which claim/observation carried which contact point (entity-resolution signal for 001E).
CREATE TABLE IF NOT EXISTS extract.contact_sightings (
    claim_id                BIGINT PRIMARY KEY REFERENCES extract.claims (claim_id),
    observation_id          BIGINT NOT NULL REFERENCES extract.observations (observation_id),
    contact_hash            TEXT NOT NULL REFERENCES extract.contact_points (contact_hash)
);
CREATE INDEX IF NOT EXISTS idx_contact_sightings_hash ON extract.contact_sightings (contact_hash);

-- "Current" observation per source record: highest run_id among non-retired runs (001C §2).
CREATE OR REPLACE VIEW extract.current_observations AS
SELECT DISTINCT ON (o.record_key) o.*
FROM extract.observations o
JOIN extract.runs r ON r.run_id = o.run_id
WHERE NOT r.retired
ORDER BY o.record_key, o.run_id DESC;
