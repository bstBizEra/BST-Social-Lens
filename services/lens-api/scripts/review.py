#!/usr/bin/env python3
"""Review CSV round-trip (SLL-PROP-DATA-001G §6) — the workflow until the Portal exists.

  export --queue {extraction|location} --limit N --out DIR     write one CSV of open items with evidence (contacts masked)
  import --file CSV --reviewer ID [--dry-run]                   validate, write superseding HUMAN rows + one audit row per action

Queues implemented now: extraction (extract.claims) and location (geo.resolved_locations). The match queue
needs resolution.* (001F) and is refused until then. CSV files are working aids: not committed, deleted after import.

Actions (001G §3): CONFIRM (HUMAN row with the same value), CORRECT (HUMAN row with `corrected_value`),
REJECT (HUMAN row marked REJECTED), DEFER (audit only; item stays open). A machine row is never edited.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from scripts.golden import _dsn, mask  # noqa: E402

ACTIONS = {"CONFIRM", "CORRECT", "REJECT", "DEFER"}
QUEUES = {"extraction", "location"}
CLAIM_FIELDS_CORRECTABLE = {"PRICE": "amount_original", "AREA": "area_sqm", "TRANSACTION_TYPE": "transaction_type",
                            "ASSET_TYPE": "asset_type", "ADVERTISER_ROLE": "advertiser_role", "LOCATION_TEXT": "value_text"}
PRECISIONS = {"EXACT_COORDINATE", "PARCEL_APPROXIMATE", "VILLAGE", "DISTRICT", "PROVINCE", "TEXT_ONLY", "UNKNOWN"}

EXT_COLS = ["claim_id", "observation_id", "record_key", "field", "value_text", "normalised", "confidence", "review_status", "signals",
            "context", "action", "corrected_value", "reason"]
LOC_COLS = ["resolved_location_id", "observation_id", "record_key", "precision", "province_code", "district_code", "village_code",
            "lat", "lng", "confidence", "signals", "context", "action", "corrected_value", "reason"]


# ---------------------------------------------------------------- export

async def export(queue: str, limit: int, out: Path) -> None:
    import asyncpg

    con = await asyncpg.connect(_dsn())
    try:
        if queue == "extraction":
            rows = await con.fetch(
                """SELECT c.claim_id, c.observation_id, o.record_key, c.field, c.value_text, c.normalised::text AS normalised, c.confidence,
                          c.review_status, c.signals::text AS signals, r.text
                   FROM extract.claims c
                   JOIN extract.current_observations o ON o.observation_id = c.observation_id
                   LEFT JOIN records r ON r.key = o.record_key
                   WHERE c.review_status IN ('LOW_CONFIDENCE','UNREVIEWED') AND c.extraction_method <> 'HUMAN'
                     AND NOT EXISTS (SELECT 1 FROM extract.claims h WHERE h.supersedes_claim_id = c.claim_id)
                     AND (c.review_status = 'LOW_CONFIDENCE' OR c.confidence < 0.5 OR o.signal_class = 'UNCERTAIN')
                   ORDER BY o.observed_at DESC LIMIT $1""", limit)
            cols = EXT_COLS
        else:
            rows = await con.fetch(
                """SELECT l.resolved_location_id, l.observation_id, o.record_key, l.precision, l.province_code, l.district_code, l.village_code,
                          l.lat, l.lng, l.confidence, l.signals::text AS signals, r.text
                   FROM geo.resolved_locations l
                   JOIN extract.current_observations o ON o.observation_id = l.observation_id
                   LEFT JOIN records r ON r.key = o.record_key
                   WHERE l.is_primary AND l.resolver_method <> 'HUMAN'
                     AND (l.review_status = 'LOW_CONFIDENCE' OR l.signals::text LIKE '%conflict%' OR l.signals::text LIKE '%ambiguous%')
                     AND NOT EXISTS (SELECT 1 FROM geo.resolved_locations h WHERE h.supersedes_id = l.resolved_location_id)
                   ORDER BY o.observed_at DESC LIMIT $1""", limit)
            cols = LOC_COLS
    finally:
        await con.close()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"review-{queue}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in rows:
            d = {k: r[k] for k in cols if k in r.keys()}
            if "normalised" in d and d["normalised"]:
                n = json.loads(d["normalised"])
                n.pop("raw_value", None)
                d["normalised"] = json.dumps(n, ensure_ascii=False)
            d["context"] = mask((r["text"] or "")[:300])
            d.update(action="", corrected_value="", reason="")
            w.writerow(d)
    print(f"exported {len(rows)} {queue} items to {path}")


# ---------------------------------------------------------------- import

def validate_rows(queue: str, rows: list[dict]) -> tuple[list[dict], list[tuple[int, str]]]:
    ok, skipped = [], []
    for i, row in enumerate(rows, start=2):
        a = (row.get("action") or "").strip().upper()
        if not a:
            continue
        if a not in ACTIONS:
            skipped.append((i, f"unknown action {a!r}"))
            continue
        cv = (row.get("corrected_value") or "").strip()
        if a == "CORRECT" and not cv:
            skipped.append((i, "CORRECT without corrected_value"))
            continue
        if queue == "extraction" and a == "CORRECT" and row.get("field") not in CLAIM_FIELDS_CORRECTABLE:
            skipped.append((i, f"field {row.get('field')} not correctable via CSV"))
            continue
        if queue == "location" and a == "CORRECT":
            parts = dict(p.split("=", 1) for p in cv.split(";") if "=" in p)
            if "precision" in parts and parts["precision"] not in PRECISIONS:
                skipped.append((i, f"precision {parts['precision']!r} outside vocabulary"))
                continue
            if not parts:
                skipped.append((i, "corrected_value must be key=value;… (precision, province_code, district_code, village_code, lat, lng)"))
                continue
        row["_action"], row["_cv"] = a, cv
        ok.append(row)
    return ok, skipped


async def import_(queue: str, file: Path, reviewer: str, dry_run: bool) -> None:
    import asyncpg

    rows = list(csv.DictReader(file.open(encoding="utf-8", newline="")))
    ok, skipped = validate_rows(queue, rows)
    for line, why in skipped:
        print(f"skip line {line}: {why}")
    batch_id = f"csv-{uuid.uuid4().hex[:12]}"
    if dry_run:
        print(f"dry-run: {len(ok)} action(s) valid, {len(skipped)} skipped; batch would be {batch_id}")
        return
    con = await asyncpg.connect(_dsn())
    done = 0
    try:
        async with con.transaction():
            for row in ok:
                if queue == "extraction":
                    done += await _apply_claim(con, row, reviewer, batch_id)
                else:
                    done += await _apply_location(con, row, reviewer, batch_id)
    finally:
        await con.close()
    print(f"imported {done} action(s) in batch {batch_id}; {len(skipped)} skipped")


async def _apply_claim(con, row: dict, reviewer: str, batch_id: str) -> int:
    cid = int(row["claim_id"])
    orig = await con.fetchrow("SELECT * FROM extract.claims WHERE claim_id=$1", cid)
    if not orig:
        print(f"skip claim {cid}: not found")
        return 0
    if await con.fetchval("SELECT 1 FROM extract.claims WHERE supersedes_claim_id=$1", cid):
        print(f"skip claim {cid}: already reviewed")
        return 0
    a, cv = row["_action"], row["_cv"]
    normalised = json.loads(orig["normalised"]) if isinstance(orig["normalised"], str) else dict(orig["normalised"] or {})
    if a == "CORRECT":
        key = CLAIM_FIELDS_CORRECTABLE[orig["field"]]
        if key == "value_text":
            value_text = cv
        else:
            value_text = orig["value_text"]
            normalised[key] = cv
    else:
        value_text = orig["value_text"]
    status = {"CONFIRM": "CONFIRMED", "CORRECT": "CORRECTED", "REJECT": "REJECTED"}.get(a)
    if status:
        await con.execute(
            """INSERT INTO extract.claims (observation_id, field, value_text, span_start, span_end, extraction_method, method_version, confidence,
                                           review_status, normalised, normalisation_status, signals, supersedes_claim_id, reviewer)
               VALUES ($1,$2,$3,$4,$5,'HUMAN','csv-1',$6,$7,$8::jsonb,$9,'[]'::jsonb,$10,$11)""",
            orig["observation_id"], orig["field"], value_text, orig["span_start"], orig["span_end"], 1.0 if a != "REJECT" else 0.0,
            status, json.dumps(normalised, ensure_ascii=False), orig["normalisation_status"], cid, reviewer)
    await con.execute(
        "INSERT INTO audit.events (actor, role, queue, item_table, item_id, action, before, after, reason, batch_id) VALUES ($1,'reviewer','extraction','extract.claims',$2,$3,$4::jsonb,$5::jsonb,$6,$7)",
        reviewer, str(cid), a, json.dumps({"value_text": orig["value_text"], "review_status": orig["review_status"]}),
        json.dumps({"value_text": value_text, "review_status": status or "DEFERRED"}), row.get("reason") or None, batch_id)
    return 1


async def _apply_location(con, row: dict, reviewer: str, batch_id: str) -> int:
    lid = int(row["resolved_location_id"])
    orig = await con.fetchrow("SELECT * FROM geo.resolved_locations WHERE resolved_location_id=$1", lid)
    if not orig:
        print(f"skip location {lid}: not found")
        return 0
    if await con.fetchval("SELECT 1 FROM geo.resolved_locations WHERE supersedes_id=$1", lid):
        print(f"skip location {lid}: already reviewed")
        return 0
    a, cv = row["_action"], row["_cv"]
    new = {k: orig[k] for k in ("precision", "point_source", "province_code", "district_code", "village_code", "lat", "lng")}
    if a == "CORRECT":
        for k, v in (p.split("=", 1) for p in cv.split(";") if "=" in p):
            k = k.strip()
            if k in ("lat", "lng"):
                new[k] = float(v)
            elif k in new:
                new[k] = v.strip() or None
        if new["precision"] in ("EXACT_COORDINATE", "PARCEL_APPROXIMATE") and new["point_source"] not in ("MAP_URL", "TEXT_COORDINATE"):
            new["point_source"] = "TEXT_COORDINATE"
        elif new["precision"] not in ("EXACT_COORDINATE", "PARCEL_APPROXIMATE") and new["point_source"] in ("MAP_URL", "TEXT_COORDINATE"):
            new["point_source"] = "NONE"
    status = {"CONFIRM": "CONFIRMED", "CORRECT": "CORRECTED", "REJECT": "REJECTED"}.get(a)
    if status:
        await con.execute(
            """INSERT INTO geo.resolved_locations (observation_id, run_id, input_claim_ids, admin_version, province_code, district_code, village_code,
                 lat, lng, precision, point_source, confidence, resolver_method, resolver_version, is_primary, signals, review_status, supersedes_id, reviewer)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'HUMAN','csv-1',$13,'["human_review"]'::jsonb,$14,$15,$16)""",
            orig["observation_id"], orig["run_id"], orig["input_claim_ids"], orig["admin_version"], new["province_code"], new["district_code"],
            new["village_code"], new["lat"], new["lng"], new["precision"], new["point_source"], 1.0 if a != "REJECT" else 0.0,
            a != "REJECT", status, lid, reviewer)
    await con.execute(
        "INSERT INTO audit.events (actor, role, queue, item_table, item_id, action, before, after, reason, batch_id) VALUES ($1,'reviewer','location','geo.resolved_locations',$2,$3,$4::jsonb,$5::jsonb,$6,$7)",
        reviewer, str(lid), a, json.dumps({k: orig[k] for k in ("precision", "village_code", "district_code")}),
        json.dumps({"precision": new["precision"], "village_code": new["village_code"], "district_code": new["district_code"], "review_status": status or "DEFERRED"}),
        row.get("reason") or None, batch_id)
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--queue", required=True, choices=sorted(QUEUES | {"match"}))
    e.add_argument("--limit", type=int, default=100)
    e.add_argument("--out", required=True)
    i = sub.add_parser("import")
    i.add_argument("--file", required=True)
    i.add_argument("--reviewer", default=os.environ.get("LENS_REVIEWER", ""))
    i.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "export":
        if a.queue == "match":
            sys.exit("match queue needs resolution.* (001F) — not yet applied")
        asyncio.run(export(a.queue, a.limit, Path(a.out)))
    else:
        if not a.reviewer:
            sys.exit("--reviewer (or LENS_REVIEWER) is required")
        q = "extraction" if re.search(r"review-extraction", Path(a.file).name) else "location" if re.search(r"review-location", Path(a.file).name) else None
        if not q:
            sys.exit("file name must be review-extraction.csv or review-location.csv (export output)")
        asyncio.run(import_(q, Path(a.file), a.reviewer, a.dry_run))
