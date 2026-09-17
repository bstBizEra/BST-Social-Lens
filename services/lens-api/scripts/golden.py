#!/usr/bin/env python3
"""Golden-set tooling for the Phase 6 acceptance gates (001C §10.1, 001D §9.2; decision C5).

  export  — pull N real records from LensDB into two CSVs for hand-labelling (text only; phone
            numbers masked; no author or contact raw values leave the database):
              golden-records.csv    key, record_type, post_date, text, RULE_V1 suggestion columns, empty label columns
              golden-locations.csv  key, location_text/url, resolver suggestion, empty label columns
  build   — turn the labelled CSVs into tests/fixtures/extract/golden-v1.jsonl and tests/fixtures/geo/golden-v1.jsonl

The labeller fills the `label_*` columns; blanks mean "not present / not labelled" and are skipped by
the gates. The suggestion columns are there to speed labelling, not to be copied blindly (I2).

Usage (inside WSL, from services/lens-api, DSN read from .env):
  python scripts/golden.py export --out /path/dir --limit 120 [--since 2026-09-17]
  python scripts/golden.py build  --records /path/golden-records.csv --locations /path/golden-locations.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from app.extract import extract_observation  # noqa: E402
from app.geo.gazetteer import Gazetteer  # noqa: E402
from app.geo.resolver import resolve  # noqa: E402

_PHONE = re.compile(r"(?<!\d)(?:\+?856[\s-]?|0)(?:20[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{3}|20[\s-]?\d{4}[\s-]?\d{4}|(?:21|23|30|31|34|36|38|41|51|54|61|64|71|74|81|84|86)[\s-]?\d{3}[\s-]?\d{3})(?!\d)")

REC_COLS = ["key", "record_type", "post_date", "text",
            "suggest_signal_class", "suggest_asset_type", "suggest_amount_original", "suggest_currency", "suggest_area_sqm",
            "label_signal_class", "label_asset_type", "label_amount_original", "label_currency", "label_area_sqm", "label_note"]
LOC_COLS = ["key", "location_text", "suggest_precision", "suggest_province_code", "suggest_district_code", "suggest_village_code",
            "label_precision", "label_province_code", "label_district_code", "label_village_code", "label_note"]


def _dsn() -> str:
    dsn = os.environ.get("LENS_DB_DSN")
    if dsn:
        return dsn
    env = HERE / ".env"
    kv = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip().strip('"')
    user, pw, db = kv.get("POSTGRES_USER", "lens"), kv.get("POSTGRES_PASSWORD", ""), kv.get("POSTGRES_DB", "lens")
    return f"postgresql://{user}:{pw}@127.0.0.1:5432/{db}"


def mask(text: str) -> str:
    return _PHONE.sub(lambda m: re.sub(r"\d", "X", m.group(0)[:-2]) + m.group(0)[-2:], text)


async def export(out: Path, limit: int, since: str | None) -> None:
    import asyncpg

    con = await asyncpg.connect(_dsn())
    try:
        rows = await con.fetch(
            "SELECT key, record_type, created_at, text FROM records WHERE text IS NOT NULL AND length(text) > 10 AND ($1::timestamptz IS NULL OR captured_at >= $1) ORDER BY random() LIMIT $2",
            datetime.fromisoformat(since).replace(tzinfo=timezone.utc) if since else None, limit,
        )
        ver = await con.fetchval("SELECT admin_version FROM geo.admin_versions WHERE is_current LIMIT 1")
        gaz = None
        if ver:
            p = await con.fetch("SELECT code, name_lo, name_en, name_variants, centroid_lat, centroid_lng FROM geo.provinces WHERE admin_version=$1", ver)
            d = await con.fetch("SELECT code, province_code, name_lo, name_en, name_variants, centroid_lat, centroid_lng FROM geo.districts WHERE admin_version=$1", ver)
            v = await con.fetch("SELECT code, district_code, province_code, name_lo, name_en, name_variants, centroid_lat, centroid_lng FROM geo.villages WHERE admin_version=$1", ver)
            a = await con.fetch("SELECT level, code, alias FROM geo.name_aliases")
            gaz = Gazetteer.from_rows(ver, [dict(r) for r in p], [dict(r) for r in d], [dict(r) for r in v], [dict(r) for r in a])
    finally:
        await con.close()

    out.mkdir(parents=True, exist_ok=True)
    with (out / "golden-records.csv").open("w", newline="", encoding="utf-8") as f_r, (out / "golden-locations.csv").open("w", newline="", encoding="utf-8") as f_l:
        wr, wl = csv.DictWriter(f_r, REC_COLS), csv.DictWriter(f_l, LOC_COLS)
        wr.writeheader()
        wl.writeheader()
        n_loc = 0
        for r in rows:
            text = mask(r["text"])
            obs = extract_observation(text, r["record_type"])
            price = next((c for c in obs.claims if c.field == "PRICE"), None)
            area = next((c for c in obs.claims if c.field == "AREA"), None)
            wr.writerow({
                "key": r["key"], "record_type": r["record_type"], "post_date": r["created_at"].date().isoformat() if r["created_at"] else "",
                "text": text, "suggest_signal_class": obs.signal_class, "suggest_asset_type": obs.asset_type,
                "suggest_amount_original": price.normalised["amount_original"] if price else "", "suggest_currency": price.normalised["currency_original"] if price else "",
                "suggest_area_sqm": area.normalised["area_sqm"] if area else "",
            })
            locs = [c for c in obs.claims if c.field in ("LOCATION_TEXT", "MAP_URL", "COORDINATE")]
            if locs:
                res = resolve(obs.claims, gaz)
                prim = next((x for x in res if x.is_primary), None)
                wl.writerow({
                    "key": r["key"], "location_text": " | ".join(c.value_text for c in locs),
                    "suggest_precision": prim.precision if prim else "", "suggest_province_code": (prim.province_code or "") if prim else "",
                    "suggest_district_code": (prim.district_code or "") if prim else "", "suggest_village_code": (prim.village_code or "") if prim else "",
                })
                n_loc += 1
    print(f"exported {len(rows)} records and {n_loc} location rows to {out} (gazetteer: {gaz.admin_version if gaz else 'none'})")


def build(records_csv: Path | None, locations_csv: Path | None) -> None:
    if records_csv:
        out = HERE / "tests" / "fixtures" / "extract" / "golden-v1.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with records_csv.open(encoding="utf-8", newline="") as f, out.open("w", encoding="utf-8") as o:
            for row in csv.DictReader(f):
                if not row.get("label_signal_class"):
                    continue
                item = {"text": row["text"], "record_type": row.get("record_type") or "post", "signal_class": row["label_signal_class"].strip()}
                if row.get("label_asset_type"):
                    item["asset_type"] = row["label_asset_type"].strip()
                if row.get("label_amount_original"):
                    item["amount_original"] = row["label_amount_original"].strip().replace(",", "")
                if row.get("label_area_sqm"):
                    item["area_sqm"] = row["label_area_sqm"].strip()
                o.write(json.dumps(item, ensure_ascii=False) + "\n")
                n += 1
        print(f"wrote {n} labelled records to {out}")
    if locations_csv:
        out = HERE / "tests" / "fixtures" / "geo" / "golden-v1.jsonl"
        n = 0
        with locations_csv.open(encoding="utf-8", newline="") as f, out.open("w", encoding="utf-8") as o:
            for row in csv.DictReader(f):
                if not row.get("label_precision"):
                    continue
                item = {"text": row["location_text"], "precision": row["label_precision"].strip()}
                for k in ("province_code", "district_code", "village_code"):
                    if row.get(f"label_{k}"):
                        item[k] = row[f"label_{k}"].strip()
                o.write(json.dumps(item, ensure_ascii=False) + "\n")
                n += 1
        print(f"wrote {n} labelled locations to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    e.add_argument("--limit", type=int, default=120)
    e.add_argument("--since")
    b = sub.add_parser("build")
    b.add_argument("--records")
    b.add_argument("--locations")
    a = ap.parse_args()
    if a.cmd == "export":
        asyncio.run(export(Path(a.out), a.limit, a.since))
    else:
        build(Path(a.records) if a.records else None, Path(a.locations) if a.locations else None)
