#!/usr/bin/env python3
"""Review CSV round-trip (SLL-PROP-DATA-001G §6) — the workflow until the Portal exists.

  export --queue {extraction|location|match} --limit N --out DIR   write one CSV of open items with evidence (contacts masked)
  import --file CSV --reviewer ID [--dry-run]                       validate, write superseding HUMAN rows + one audit row per action

Queues: extraction (extract.claims), location (geo.resolved_locations), match (resolution.current_decisions —
needs `LENS_RESOLUTION_ENABLED=1` so resolution.* exists; refused otherwise). CSV files are working aids: not
committed, deleted after import.

Actions (001G §3): CONFIRM (HUMAN row with the same value), CORRECT (HUMAN row with `corrected_value`),
REJECT (HUMAN row marked REJECTED), DEFER (audit only; item stays open). A machine row is never edited.

Match queue semantics (001E §7 / 001G §3): an item is an observation whose current machine decision is
REVIEW_REQUIRED — it sits on its own provisional property (`market_property_id`) with a proposed peer property
(`proposed_property_id`, the best-scoring candidate). CONFIRM = the proposed match is right → HUMAN `CONFIRMED`
on `proposed_property_id`; CORRECT = link to another property → `corrected_value` = an ACTIVE `MP-…` id;
REJECT = the proposed match is wrong, the observation is its own property → HUMAN `CONFIRMED` on its current
provisional property (evidence is never dropped — the rejected target is recorded in the audit row);
DEFER = audit only. A provisional property left with no current observation is marked SUPERSEDED by the
target with a MERGE transition, and statistics snapshots are recomputed for every touched property.
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
QUEUES = {"extraction", "location", "match"}
CLAIM_FIELDS_CORRECTABLE = {"PRICE": "amount_original", "AREA": "area_sqm", "TRANSACTION_TYPE": "transaction_type",
                            "ASSET_TYPE": "asset_type", "ADVERTISER_ROLE": "advertiser_role", "LOCATION_TEXT": "value_text"}
PRECISIONS = {"EXACT_COORDINATE", "PARCEL_APPROXIMATE", "VILLAGE", "DISTRICT", "PROVINCE", "TEXT_ONLY", "UNKNOWN"}

EXT_COLS = ["claim_id", "observation_id", "record_key", "field", "value_text", "normalised", "confidence", "review_status", "signals",
            "context", "action", "corrected_value", "reason"]
MATCH_COLS = ["decision_id", "observation_id", "record_key", "market_property_id", "proposed_property_id", "proposed_peer_record_key",
              "candidate_id", "score", "forced_rule", "signals", "blocking_reasons", "other_candidates", "context", "action", "corrected_value", "reason"]
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
        elif queue == "match":
            if not await con.fetchval("SELECT to_regclass('resolution.current_decisions') IS NOT NULL"):
                sys.exit("match queue needs resolution.* — start lens-api once with LENS_RESOLUTION_ENABLED=1 (001F draft schema)")
            rows = await con.fetch(
                """SELECT d.decision_id, d.observation_id, o.record_key, d.market_property_id,
                          c.market_property_id AS proposed_property_id, po.record_key AS proposed_peer_record_key,
                          c.candidate_id, c.score, c.forced_rule, c.signals::text AS signals, c.blocking_reasons::text AS blocking_reasons,
                          (SELECT string_agg(x.market_property_id || ':' || x.score, ' ' ORDER BY x.score DESC)
                             FROM resolution.entity_candidates x
                             WHERE x.observation_id = d.observation_id AND x.candidate_id <> c.candidate_id AND x.market_property_id IS NOT NULL) AS other_candidates,
                          r.text
                   FROM resolution.current_decisions d
                   JOIN extract.observations o ON o.observation_id = d.observation_id
                   LEFT JOIN resolution.entity_candidates c ON c.candidate_id = d.candidate_id
                   LEFT JOIN extract.observations po ON po.observation_id = c.peer_observation_id
                   LEFT JOIN records r ON r.key = o.record_key
                   WHERE d.decision = 'REVIEW_REQUIRED' AND d.source <> 'HUMAN'
                   ORDER BY c.score DESC NULLS LAST, d.decision_id LIMIT $1""", limit)
            cols = MATCH_COLS
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
        if queue == "match" and a == "CORRECT" and not re.fullmatch(r"MP-[0-9A-HJKMNP-TV-Z]{26}", cv):
            skipped.append((i, "corrected_value must be an MP-<ULID> market property id"))
            continue
        if queue == "match" and a == "CONFIRM" and not (row.get("proposed_property_id") or "").strip():
            skipped.append((i, "CONFIRM needs a proposed_property_id (no candidate) — use CORRECT with a property id or REJECT"))
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
    touched: set[str] = set()
    try:
        async with con.transaction():
            for row in ok:
                if queue == "extraction":
                    done += await _apply_claim(con, row, reviewer, batch_id)
                elif queue == "match":
                    done += await _apply_match(con, row, reviewer, batch_id, touched)
                else:
                    done += await _apply_location(con, row, reviewer, batch_id)
            if touched:
                from app.resolution.store import ResolutionStore  # snapshots (001E §9) for every property a decision touched

                for mp in sorted(touched):
                    await ResolutionStore.snapshot_stats(None, con, mp)
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


async def _apply_match(con, row: dict, reviewer: str, batch_id: str, touched: set[str]) -> int:
    did = int(row["decision_id"])
    orig = await con.fetchrow("SELECT * FROM resolution.entity_decisions WHERE decision_id=$1", did)
    if not orig:
        print(f"skip decision {did}: not found")
        return 0
    if await con.fetchval("SELECT 1 FROM resolution.entity_decisions WHERE supersedes_decision_id=$1", did):
        print(f"skip decision {did}: already reviewed")
        return 0
    a, cv = row["_action"], row["_cv"]
    own = orig["market_property_id"]
    proposed = (row.get("proposed_property_id") or "").strip() or None
    other = {"CONFIRM": proposed, "CORRECT": cv or None}.get(a)
    moved = 0
    survivor = own
    if a == "REJECT":
        # the proposed match is wrong: the observation is its own property — HUMAN CONFIRMED on it (evidence kept)
        await con.execute(
            "INSERT INTO resolution.entity_decisions (observation_id, market_property_id, decision, source, reviewer, supersedes_decision_id) VALUES ($1,$2,'CONFIRMED','HUMAN',$3,$4)",
            orig["observation_id"], own, reviewer, did)
        touched.add(own)
    elif other:
        st = await con.fetchrow("SELECT status, superseded_by FROM market.properties WHERE market_property_id=$1", other)
        if not st:
            print(f"skip decision {did}: property {other} not found")
            return 0
        if st["status"] == "SUPERSEDED":  # follow one merge hop (reviewers may hold stale ids after other merges)
            other = st["superseded_by"]
        if other == own:
            await con.execute(
                "INSERT INTO resolution.entity_decisions (observation_id, market_property_id, decision, source, reviewer, candidate_id, supersedes_decision_id) VALUES ($1,$2,'CONFIRMED','HUMAN',$3,$4,$5)",
                orig["observation_id"], own, reviewer, orig["candidate_id"], did)
            touched.add(own)
        else:
            # "same property": MERGE — the property with more current observations survives (tie → older id); every current
            # observation of the absorbed one gets a HUMAN CONFIRMED decision on the survivor; nothing is deleted.
            counts = {}
            for mp in (own, other):
                counts[mp] = await con.fetchval("SELECT count(*) FROM resolution.current_decisions WHERE market_property_id=$1 AND decision NOT IN ('REJECTED','UNLINKED')", mp)
            survivor = other if counts[other] > counts[own] else own if counts[own] > counts[other] else min(own, other)
            absorbed = other if survivor == own else own
            cur = await con.fetch("SELECT decision_id, observation_id, candidate_id FROM resolution.current_decisions WHERE market_property_id=$1 AND decision NOT IN ('REJECTED','UNLINKED') ORDER BY decision_id", absorbed)
            for d in cur:
                await con.execute(
                    "INSERT INTO resolution.entity_decisions (observation_id, market_property_id, decision, source, reviewer, candidate_id, supersedes_decision_id) VALUES ($1,$2,'CONFIRMED','HUMAN',$3,$4,$5)",
                    d["observation_id"], survivor, reviewer, orig["candidate_id"] if d["decision_id"] == did else None, d["decision_id"])
                moved += 1
            if survivor == own:  # the reviewed item stays on `own`; attest it (when `own` is absorbed it moved with the loop above)
                await con.execute(
                    "INSERT INTO resolution.entity_decisions (observation_id, market_property_id, decision, source, reviewer, candidate_id, supersedes_decision_id) VALUES ($1,$2,'CONFIRMED','HUMAN',$3,$4,$5)",
                    orig["observation_id"], own, reviewer, orig["candidate_id"], did)
            await con.execute("INSERT INTO resolution.property_transitions (kind, source_property_id, target_property_ids, actor, reason) VALUES ('MERGE',$1,$2,$3,$4)",
                              absorbed, [survivor], reviewer, row.get("reason") or f"csv {a}")
            await con.execute("UPDATE market.properties SET status='SUPERSEDED', superseded_by=$2 WHERE market_property_id=$1", absorbed, survivor)
            touched.update((own, other))
    await con.execute(
        "INSERT INTO audit.events (actor, role, queue, item_table, item_id, action, before, after, reason, batch_id) VALUES ($1,'reviewer','match','resolution.entity_decisions',$2,$3,$4::jsonb,$5::jsonb,$6,$7)",
        reviewer, str(did), a, json.dumps({"decision": orig["decision"], "market_property_id": own, "proposed_property_id": proposed}),
        json.dumps({"decision": "CONFIRMED" if a != "DEFER" else "DEFERRED", "market_property_id": survivor, "merged_observations": moved,
                    "rejected_property_id": proposed if a == "REJECT" else None}),
        row.get("reason") or None, batch_id)
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
    e.add_argument("--queue", required=True, choices=sorted(QUEUES))
    e.add_argument("--limit", type=int, default=100)
    e.add_argument("--out", required=True)
    i = sub.add_parser("import")
    i.add_argument("--file", required=True)
    i.add_argument("--reviewer", default=os.environ.get("LENS_REVIEWER", ""))
    i.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "export":
        asyncio.run(export(a.queue, a.limit, Path(a.out)))
    else:
        if not a.reviewer:
            sys.exit("--reviewer (or LENS_REVIEWER) is required")
        m = re.search(r"review-(extraction|location|match)", Path(a.file).name)
        q = m.group(1) if m else None
        if not q:
            sys.exit("file name must be review-extraction.csv, review-location.csv or review-match.csv (export output)")
        asyncio.run(import_(q, Path(a.file), a.reviewer, a.dry_run))
