-- BST Social Lens — L2 geography subset (SLL-PROP-DATA-001D; 001F partial DDL). ADDITIVE ONLY.
-- Applied idempotently by db.init_db() after schema.sql / schema_extract.sql; schema_lint enforces
-- that nothing here touches L0/L1 and that no `parcel` / `title` / `owner` column exists (I5).
--
-- Geometry: PostGIS is a Phase 6 operator step (001D G1). Until it is present, polygons are kept
-- as GeoJSON text and only centroids are used (text path). A later additive migration adds
-- `geom geometry(MultiPolygon,4326)` columns and `ST_Within` for the point path.

CREATE SCHEMA IF NOT EXISTS geo;

-- Versioned, immutable copies of the BST Lao Data Map export (D4). New version = new rows.
CREATE TABLE IF NOT EXISTS geo.admin_versions (
    admin_version   TEXT PRIMARY KEY,                       -- e.g. 'laodatamap-2026.09'
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_ref      TEXT NOT NULL,                          -- upstream file/commit reference
    checksum        TEXT NOT NULL,                          -- sha256 of the imported payload
    note            TEXT,
    is_current      BOOLEAN NOT NULL DEFAULT false          -- exactly one current version (enforced by the import step)
);

CREATE TABLE IF NOT EXISTS geo.provinces (
    admin_version   TEXT NOT NULL REFERENCES geo.admin_versions (admin_version),
    code            TEXT NOT NULL,                          -- upstream code, never re-keyed
    name_lo         TEXT NOT NULL,
    name_en         TEXT,
    name_variants   TEXT[] NOT NULL DEFAULT '{}',
    centroid_lat    DOUBLE PRECISION,
    centroid_lng    DOUBLE PRECISION,
    geom_geojson    JSONB,                                  -- MultiPolygon; loaded into a PostGIS column later
    PRIMARY KEY (admin_version, code)
);

CREATE TABLE IF NOT EXISTS geo.districts (
    admin_version   TEXT NOT NULL REFERENCES geo.admin_versions (admin_version),
    code            TEXT NOT NULL,
    province_code   TEXT NOT NULL,
    name_lo         TEXT NOT NULL,
    name_en         TEXT,
    name_variants   TEXT[] NOT NULL DEFAULT '{}',
    centroid_lat    DOUBLE PRECISION,
    centroid_lng    DOUBLE PRECISION,
    geom_geojson    JSONB,
    PRIMARY KEY (admin_version, code),
    FOREIGN KEY (admin_version, province_code) REFERENCES geo.provinces (admin_version, code)
);

CREATE TABLE IF NOT EXISTS geo.villages (
    admin_version   TEXT NOT NULL REFERENCES geo.admin_versions (admin_version),
    code            TEXT NOT NULL,
    district_code   TEXT NOT NULL,
    province_code   TEXT NOT NULL,
    name_lo         TEXT NOT NULL,
    name_en         TEXT,
    name_variants   TEXT[] NOT NULL DEFAULT '{}',
    centroid_lat    DOUBLE PRECISION,
    centroid_lng    DOUBLE PRECISION,
    geom_geojson    JSONB,
    PRIMARY KEY (admin_version, code),
    FOREIGN KEY (admin_version, district_code) REFERENCES geo.districts (admin_version, code)
);
CREATE INDEX IF NOT EXISTS idx_villages_name_lo ON geo.villages (admin_version, name_lo);
CREATE INDEX IF NOT EXISTS idx_villages_district ON geo.villages (admin_version, district_code);

-- Social Lens-owned spelling/transliteration aliases collected from review (G3). Upstream rows stay pristine.
CREATE TABLE IF NOT EXISTS geo.name_aliases (
    alias_id        BIGSERIAL PRIMARY KEY,
    level           TEXT NOT NULL CHECK (level IN ('province','district','village')),
    code            TEXT NOT NULL,                          -- target admin code (any version; codes are stable upstream)
    alias           TEXT NOT NULL,                          -- NFC, as written in posts
    source          TEXT NOT NULL DEFAULT 'REVIEW' CHECK (source IN ('REVIEW','SEED','IMPORT')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (level, code, alias)
);

-- Resolver output (001D §3): append-only; primary = highest confidence per observation per run.
CREATE TABLE IF NOT EXISTS geo.resolved_locations (
    resolved_location_id BIGSERIAL PRIMARY KEY,
    observation_id  BIGINT NOT NULL REFERENCES extract.observations (observation_id),
    run_id          BIGINT NOT NULL REFERENCES extract.runs (run_id),
    input_claim_ids BIGINT[] NOT NULL DEFAULT '{}',         -- contributing extract.claims
    admin_version   TEXT REFERENCES geo.admin_versions (admin_version),  -- NULL when resolved without a gazetteer
    province_code   TEXT,
    district_code   TEXT,
    village_code    TEXT,
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    precision       TEXT NOT NULL CHECK (precision IN ('EXACT_COORDINATE','PARCEL_APPROXIMATE','VILLAGE','DISTRICT','PROVINCE','TEXT_ONLY','UNKNOWN')),
    point_source    TEXT NOT NULL DEFAULT 'NONE' CHECK (point_source IN ('MAP_URL','TEXT_COORDINATE','VILLAGE_CENTROID','DISTRICT_CENTROID','PROVINCE_CENTROID','NONE')),
    confidence      REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    resolver_method TEXT NOT NULL,                          -- GEO_RULE_V1 | HUMAN
    resolver_version TEXT NOT NULL,
    is_primary      BOOLEAN NOT NULL DEFAULT false,
    signals         JSONB NOT NULL DEFAULT '[]'::jsonb,
    review_status   TEXT NOT NULL DEFAULT 'UNREVIEWED' CHECK (review_status IN ('UNREVIEWED','LOW_CONFIDENCE','CONFIRMED','CORRECTED','REJECTED')),
    supersedes_id   BIGINT REFERENCES geo.resolved_locations (resolved_location_id),
    reviewer        TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((precision IN ('EXACT_COORDINATE','PARCEL_APPROXIMATE')) = (point_source IN ('MAP_URL','TEXT_COORDINATE'))),
    CHECK (precision NOT IN ('EXACT_COORDINATE','PARCEL_APPROXIMATE') OR (lat IS NOT NULL AND lng IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_resolved_obs       ON geo.resolved_locations (observation_id);
CREATE INDEX IF NOT EXISTS idx_resolved_precision ON geo.resolved_locations (precision) WHERE is_primary;
CREATE INDEX IF NOT EXISTS idx_resolved_village   ON geo.resolved_locations (village_code) WHERE is_primary;
