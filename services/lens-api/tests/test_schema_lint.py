"""Schema-lint (scripts/schema_lint.py) — the committed DDL passes, and each rule catches a violation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from schema_lint import lint_dir, lint_sql  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app"


def test_committed_ddl_is_clean():
    assert lint_dir(APP) == []


def test_r1_l2_file_may_not_touch_l1():
    v = lint_sql("schema_extract.sql", "ALTER TABLE records ADD COLUMN signal_class TEXT;", core=False)
    assert any("R1" in x and "records" in x for x in v)
    v = lint_sql("schema_geo.sql", "CREATE INDEX idx ON public.capture_events (record_key);", core=False)
    assert any("R1" in x for x in v)
    assert lint_sql("schema_extract.sql", "CREATE TABLE IF NOT EXISTS extract.runs (\n    run_id BIGSERIAL PRIMARY KEY\n);", core=False) == []


def test_r2_bare_vocabulary_rejected():
    ddl = "CREATE TABLE IF NOT EXISTS extract.x (\n    id BIGSERIAL PRIMARY KEY,\n    price NUMERIC,\n    owner TEXT,\n    parcel_id TEXT\n);"
    v = lint_sql("schema_extract.sql", ddl, core=False)
    assert {x.split("`")[3] for x in v if "R2" in x} == {"price", "owner", "parcel_id"}
    ok = "CREATE TABLE IF NOT EXISTS extract.y (\n    id BIGSERIAL PRIMARY KEY,\n    price_type TEXT,\n    advertiser_role TEXT,\n    area_sqm NUMERIC\n);"
    assert lint_sql("schema_extract.sql", ok, core=False) == []


def test_r3_confidence_required_on_claims_and_observations():
    ddl = "CREATE TABLE IF NOT EXISTS extract.observations (\n    id BIGSERIAL PRIMARY KEY,\n    signal_class TEXT NOT NULL\n);"
    assert any("R3" in x for x in lint_sql("schema_extract.sql", ddl, core=False))
    ddl = "CREATE TABLE IF NOT EXISTS extract.claims (\n    id BIGSERIAL PRIMARY KEY,\n    confidence REAL\n);"
    assert any("R3" in x for x in lint_sql("schema_extract.sql", ddl, core=False))


def test_r4_core_never_drops_or_deletes():
    assert any("R4" in x for x in lint_sql("schema.sql", "DROP TABLE records;", core=True))
    assert any("R4" in x for x in lint_sql("schema.sql", "DELETE FROM seen_links WHERE 1=1;", core=True))
    assert any("R4" in x for x in lint_sql("schema.sql", "CREATE SCHEMA IF NOT EXISTS extract;", core=True))
    assert lint_sql("schema.sql", "-- DROP TABLE mentioned in a comment only\nCREATE TABLE IF NOT EXISTS t (\n    a INT\n);", core=True) == []


def test_r5_claims_forbid_raw_value():
    ddl = ("CREATE TABLE IF NOT EXISTS extract.claims (\n    id BIGSERIAL PRIMARY KEY,\n"
           "    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),\n    normalised JSONB NOT NULL\n);")
    assert any("R5" in x for x in lint_sql("schema_extract.sql", ddl, core=False))
