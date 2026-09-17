#!/usr/bin/env python3
"""Schema-lint for LensDB DDL (ADR-0005 §2.3, 001C §10.3, 001D §9.4).

Mechanical rules, enforced in CI (`pytest tests/test_schema_lint.py` and `python scripts/schema_lint.py`):

  R1  L2/L3 files (`schema_<layer>.sql`, layer ≠ core) never touch L0/L1 tables:
      no CREATE/ALTER/DROP/TRUNCATE/DELETE/UPDATE/INSERT targeting records, raw_captures,
      capture_events, seen_links, ingest_runs, raw_payloads.
  R2  No bare fact-like column names anywhere in L2/L3: price, owner, property, area, parcel,
      title, duplicate, transaction_price (001A vocabulary: *_claim, *_observation, *_sqm,
      *_text, *_type, *_role, *_mention …). No BizProp+ identities: asset_id, parcel_id, title_id.
  R3  Every table whose name ends in `observations` or `claims` has a `confidence … NOT NULL`
      column with a 0..1 CHECK (I2, I6).
  R4  The core file (schema.sql) contains no DROP TABLE / DELETE FROM / TRUNCATE (evidence is
      never removed by DDL; retention is code, policy-driven — I1) and no L2 schema objects.
  R5  Claims tables forbid `raw_value` inside their JSON payload (D5) — presence of the
      `CHECK (NOT (normalised ? 'raw_value'))` guard is required.
  R6  `market.properties` carries no price-like column (`price*`, `asking*`, `amount*`, `*_lak`): statistics
      live only in `*_snapshots` tables (001E §9, I4/I7).
  R7  Every `*_snapshots` table has `stats_version` and `computed_at` NOT NULL (snapshots, never "the value").
  R8  Every `*decisions` / `*_links` table (append-only history) has a `supersedes_*` column and no
      `updated_at` column (rows are superseded, never edited — I8).

Exit code 1 with one line per violation; 0 when clean.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

L1_TABLES = {"records", "raw_captures", "capture_events", "seen_links", "ingest_runs", "raw_payloads"}
BARE_FORBIDDEN = {"price", "owner", "property", "area", "parcel", "title", "duplicate", "transaction_price",
                  "asset_id", "parcel_id", "title_id"}
_DDL_TARGET = re.compile(
    r"^\s*(?:CREATE\s+(?:TABLE|INDEX|UNIQUE\s+INDEX|VIEW|OR\s+REPLACE\s+VIEW)(?:\s+IF\s+NOT\s+EXISTS)?|ALTER\s+TABLE|DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    r"TRUNCATE(?:\s+TABLE)?|DELETE\s+FROM|UPDATE|INSERT\s+INTO)\s+(?:\S+\s+ON\s+)?(?P<target>[A-Za-z_][\w.]*)",
    re.I | re.M,
)
_CREATE_TABLE = re.compile(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?P<name>[\w.]+)\s*\((?P<body>.*?)\n\);", re.I | re.S)
_COL_LINE = re.compile(r"^\s*(?P<col>[a-z_][a-z0-9_]*)\s+(?P<type>[A-Z][A-Z0-9_ ,()]*)", re.M)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def lint_sql(name: str, sql: str, core: bool) -> list[str]:
    v: list[str] = []
    s = _strip_comments(sql)

    if core:
        for kw in ("DROP TABLE", "DELETE FROM", "TRUNCATE"):
            if re.search(rf"\b{kw}\b", s, re.I):
                v.append(f"{name}: R4 core schema must not contain {kw}")
        if re.search(r"CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(?!public\b)", s, re.I):
            v.append(f"{name}: R4 core schema must not create L2/L3 schemas")
        return v

    # R1 — never touch L0/L1
    for m in _DDL_TARGET.finditer(s):
        target = m.group("target").lower().split(".")[-1]
        if target in L1_TABLES:
            v.append(f"{name}: R1 statement targets L1 table `{target}` — L2 migrations may not alter L0/L1")

    for t in _CREATE_TABLE.finditer(s):
        tname, body = t.group("name"), t.group("body")
        cols = [c.group("col").lower() for c in _COL_LINE.finditer(body) if c.group("col").lower() not in ("check", "unique", "primary", "constraint", "foreign")]
        # R2 — bare vocabulary
        for c in cols:
            if c in BARE_FORBIDDEN:
                v.append(f"{name}: R2 table `{tname}` has bare column `{c}` (use the 001A vocabulary)")
        # R3 — confidence NOT NULL with 0..1 check on observation/claim tables
        short = tname.split(".")[-1]
        if short.endswith(("observations", "claims")):
            if not re.search(r"^\s*confidence\s+\w+\s+NOT\s+NULL\s+CHECK\s*\(\s*confidence\s*>=\s*0\s+AND\s+confidence\s*<=\s*1\s*\)", body, re.I | re.M) and \
               not re.search(r"^\s*\w*_confidence\s+\w+\s+NOT\s+NULL\s+CHECK", body, re.I | re.M):
                v.append(f"{name}: R3 table `{tname}` lacks a NOT NULL confidence column with a 0..1 CHECK")
        # R5 — claims never carry raw contact values
        if short.endswith("claims") and "normalised" in cols and "NOT (normalised ? 'raw_value')" not in body:
            v.append(f"{name}: R5 table `{tname}` must forbid `raw_value` inside `normalised` (D5)")
        # R6 — market.properties has no price-like columns
        if tname.lower() == "market.properties":
            bad = [c for c in cols if c.startswith(("price", "asking", "amount")) or c.endswith("_lak")]
            if bad:
                v.append(f"{name}: R6 `market.properties` must not carry price-like columns {bad} (statistics are snapshots only)")
        # R7 — snapshots carry stats_version + computed_at
        if short.endswith("_snapshots"):
            for req in ("stats_version", "computed_at"):
                if not re.search(rf"^\s*{req}\s+\w+\s+NOT\s+NULL", body, re.I | re.M):
                    v.append(f"{name}: R7 snapshot table `{tname}` needs `{req} … NOT NULL`")
        # R8 — decision/link history is superseded, never edited
        if short.endswith(("decisions", "_links")):
            if not any(c.startswith("supersedes_") for c in cols):
                v.append(f"{name}: R8 table `{tname}` needs a `supersedes_*` column (append-only history, I8)")
            if "updated_at" in cols:
                v.append(f"{name}: R8 table `{tname}` must not have `updated_at` (rows are superseded, not edited)")
    return v


def lint_dir(app_dir: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(app_dir.glob("schema*.sql")):
        core = p.name == "schema.sql"
        out += lint_sql(p.name, p.read_text(encoding="utf-8"), core)
    return out


if __name__ == "__main__":
    app = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "app"
    problems = lint_dir(app)
    for line in problems:
        print(line)
    print(f"schema-lint: {len(problems)} violation(s) in {app}")
    sys.exit(1 if problems else 0)
