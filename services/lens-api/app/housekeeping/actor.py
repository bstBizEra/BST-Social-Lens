"""HK-001 §9 Actor: the safe-action list, nothing else.

Each action: bounded (LENS_HK_MAX_ACTIONS), idempotent, writes ONE `audit.events` row (actor `system:housekeeper`,
role `system`, action `HOUSEKEEP`, queue `housekeeping`) and one `housekeeping.actions` row, then re-runs the check that
raised the finding (VERIFY) and moves the finding to ACTIONED/RESOLVED. Anything outside this table is a finding for a human.

    finding                  action                what it calls (existing services only)
    EXTRACTION_MISSING/STALE REEXTRACT             extract.service.run_extraction (append-only)
    SNAPSHOT_STALE           RECOMPUTE_SNAPSHOTS   resolution.store.recompute_snapshots (new rows)
    RETENTION_OVERDUE        PURGE_BY_POLICY       db.purge_raw_bodies + db.purge_records (frozen policy, protected rows exempt)
    RAW_MISSING              MARK_RAW_NEEDED       housekeeping.raw_needed (server-side ask; the extension re-sends on sync)
    RECORD_WITHOUT_EVENT     BACKFILL_EVENTS       capture_events from records.captured_at (provenance, not evidence)
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .status import housekeeping_status

ACTOR = "system:housekeeper"
ACTION_FOR: dict[str, str] = {
    "EXTRACTION_MISSING": "REEXTRACT",
    "EXTRACTION_STALE": "REEXTRACT",
    "SNAPSHOT_STALE": "RECOMPUTE_SNAPSHOTS",
    "RETENTION_OVERDUE": "PURGE_BY_POLICY",
    "RAW_MISSING": "MARK_RAW_NEEDED",
    "RECORD_WITHOUT_EVENT": "BACKFILL_EVENTS",
}


class Services:
    """Callables the Actor is allowed to use — injected by main.py so the Actor never reaches into globals."""

    def __init__(self, *, reextract: Callable[[], Awaitable[dict]], recompute: Callable[[], Awaitable[dict]] | None,
                 purge: Callable[[], Awaitable[dict]]) -> None:
        self.reextract, self.recompute, self.purge = reextract, recompute, purge


async def _audit(con: Any, action_type: str, finding: dict[str, Any], result: dict[str, Any]) -> int:
    return await con.fetchval(
        """INSERT INTO audit.events (actor, role, queue, item_table, item_id, action, before, after, reason, batch_id)
           VALUES ($1,'system','housekeeping','housekeeping.findings',$2,'HOUSEKEEP',$3::jsonb,$4::jsonb,$5,$6) RETURNING event_id""",
        ACTOR, str(finding["finding_id"]),
        json.dumps({"finding_type": finding["finding_type"], "count": finding["count"], "observed_state": finding["observed_state"]}),
        json.dumps({"action": action_type, **result}, default=str),
        f"HK-001 §9 safe action for {finding['finding_type']}", f"hk-run-{finding['run_id']}")


async def act(db: Any, *, run_id: int, services: Services, max_actions: int, config: dict[str, Any]) -> dict[str, Any]:
    """Take safe actions for OPEN auto-allowed findings of this run; verify; return counts."""
    taken: list[dict[str, Any]] = []
    async with db.pool.acquire() as con:
        findings = await con.fetch(
            """SELECT finding_id, finding_type, count, observed_state FROM housekeeping.findings
               WHERE status='OPEN' AND auto_action_allowed AND last_seen_run_id=$1 ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'ERROR' THEN 1 WHEN 'WARN' THEN 2 ELSE 3 END""", run_id)
    for f in findings:
        f = {**dict(f), "run_id": run_id}
        atype = ACTION_FOR.get(f["finding_type"])
        if not atype:
            continue
        result: dict[str, Any]
        try:
            if atype == "REEXTRACT":
                result = await services.reextract()
            elif atype == "RECOMPUTE_SNAPSHOTS":
                if services.recompute is None:
                    continue
                result = await services.recompute()
            elif atype == "PURGE_BY_POLICY":
                result = await services.purge()
            elif atype == "MARK_RAW_NEEDED":
                async with db.pool.acquire() as con:
                    n = await con.fetchval(
                        """WITH need AS (SELECT DISTINCT r.first_payload_hash h FROM records r WHERE r.first_payload_hash IS NOT NULL
                                         AND NOT EXISTS (SELECT 1 FROM raw_captures c WHERE c.payload_hash=r.first_payload_hash) LIMIT $1),
                                ins AS (INSERT INTO housekeeping.raw_needed (payload_hash, run_id) SELECT h, $2 FROM need ON CONFLICT (payload_hash) DO UPDATE SET run_id=EXCLUDED.run_id, asked_at=now() RETURNING 1)
                           SELECT count(*) FROM ins""", max_actions, run_id)
                result = {"marked": n}
            elif atype == "BACKFILL_EVENTS":
                async with db.pool.acquire() as con:
                    n = await con.fetchval(
                        """WITH ins AS (INSERT INTO capture_events (record_key, payload_hash, captured_at, page_url, parser_version, ingest_source, context)
                                        SELECT key, first_payload_hash, captured_at, NULL, parser_version, 'housekeeper:backfill', NULL FROM records r
                                        WHERE NOT EXISTS (SELECT 1 FROM capture_events e WHERE e.record_key=r.key) LIMIT $1
                                        ON CONFLICT DO NOTHING RETURNING 1)
                           SELECT count(*) FROM ins""", max_actions)
                result = {"backfilled": n}
            else:
                continue
        except Exception as e:  # noqa: BLE001 — a failed action leaves the finding OPEN; the failure is recorded
            result = {"error": f"{type(e).__name__}: {e}"}
        async with db.pool.acquire() as con:
            async with con.transaction():
                ev = await _audit(con, atype, f, result)
                await con.execute(
                    "INSERT INTO housekeeping.actions (run_id, finding_id, action_type, target_type, target_id, finished_at, result, audit_event_id) VALUES ($1,$2,$3,'aggregate',NULL,now(),$4::jsonb,$5)",
                    run_id, f["finding_id"], atype, json.dumps(result, default=str), ev)
                if "error" not in result:
                    await con.execute("UPDATE housekeeping.findings SET status='ACTIONED' WHERE finding_id=$1", f["finding_id"])
        taken.append({"finding_type": f["finding_type"], "action": atype, "result": result})
    # ---- VERIFY: recompute; findings no longer detected resolve (the next persist pass does the same)
    verified = 0
    if taken:
        st = await housekeeping_status(db, **config)
        still = {x["finding_type"] for x in st["findings"]}
        async with db.pool.acquire() as con:
            for t in taken:
                if t["finding_type"] not in still and "error" not in t["result"]:
                    await con.execute("UPDATE housekeeping.findings SET status='RESOLVED', resolved_run_id=$2, resolved_at=now() WHERE finding_type=$1 AND status='ACTIONED'", t["finding_type"], run_id)
                    verified += 1
        async with db.pool.acquire() as con:
            await con.execute("UPDATE housekeeping.runs SET actions_taken=$2 WHERE run_id=$1", run_id, len(taken))
    return {"actions": taken, "verified_resolved": verified}
