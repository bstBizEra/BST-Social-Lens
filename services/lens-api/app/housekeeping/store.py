"""HK-001 §10 persistence: one run row, per-check rows, latest watermark per stage, findings upserted by identity.
Findings are Housekeeper state (not evidence): re-detection bumps `last_seen_run_id`; disappearance resolves; nothing is deleted.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .status import HK_VERSION, housekeeping_status

_STAGE_COLS = ("last_success_at", "last_failure_at", "records_in", "records_pending", "oldest_pending_at", "lag_seconds", "method_version", "behind_version")


def _j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


async def run_housekeeping(db: Any, *, trigger: str, dry_run: bool = False, **config: Any) -> dict[str, Any]:
    """Observe + measure + reconcile + classify, then persist (unless dry_run). Actions arrive in v0.2."""
    t0 = time.monotonic()
    async with db.pool.acquire() as con:
        run_id = await con.fetchval("INSERT INTO housekeeping.runs (trigger, hk_version, dry_run) VALUES ($1,$2,$3) RETURNING run_id", trigger, HK_VERSION, dry_run)
    error: str | None = None
    status: dict[str, Any] = {}
    try:
        status = await housekeeping_status(db, **config)
        if not dry_run:
            async with db.pool.acquire() as con:
                async with con.transaction():
                    for stage, w in status["watermarks"].items():
                        vals = {c: w.get(c) for c in _STAGE_COLS}
                        detail = {k: v for k, v in w.items() if k not in _STAGE_COLS and k != "enabled"}
                        await con.execute(
                            """INSERT INTO housekeeping.watermarks (stage, run_id, enabled, last_success_at, last_failure_at, records_in, records_pending, oldest_pending_at, lag_seconds, method_version, behind_version, detail, computed_at)
                               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,now())
                               ON CONFLICT (stage) DO UPDATE SET run_id=EXCLUDED.run_id, enabled=EXCLUDED.enabled, last_success_at=EXCLUDED.last_success_at, last_failure_at=EXCLUDED.last_failure_at,
                                 records_in=EXCLUDED.records_in, records_pending=EXCLUDED.records_pending, oldest_pending_at=EXCLUDED.oldest_pending_at, lag_seconds=EXCLUDED.lag_seconds,
                                 method_version=EXCLUDED.method_version, behind_version=EXCLUDED.behind_version, detail=EXCLUDED.detail, computed_at=now()""",
                            stage, run_id, w.get("enabled", True), vals["last_success_at"], vals["last_failure_at"], vals["records_in"], vals["records_pending"],
                            vals["oldest_pending_at"], vals["lag_seconds"], vals["method_version"], vals["behind_version"], _j(detail))
                    for code, c in status["reconciliation"].items():
                        health = status["health"].get({"R-EVID": "raw", "R-SIGHT": "capture", "R-EXTR": "extract", "R-GEO": "geo", "R-RES": "resolve", "R-RET": "retention"}.get(code, ""), None)
                        if health is None:
                            health = "disabled" if c.get("ratio") is None and c.get("expected") is None else ("healthy" if (c.get("ratio") or 0) >= 0.99 else "degraded" if (c.get("ratio") or 0) >= 0.9 else "failing")
                        await con.execute("INSERT INTO housekeeping.checks (run_id, check_code, expected, observed, ratio, health, note) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                                          run_id, code, c.get("expected"), c.get("observed"), c.get("ratio"), health, c.get("note"))
                    seen: set[str] = set()
                    for f in status["findings"]:
                        seen.add(f["finding_type"])
                        await con.execute(
                            """INSERT INTO housekeeping.findings (finding_type, severity, entity_type, entity_id, count, expected_state, observed_state, recommended_action, auto_action_allowed, first_seen_run_id, last_seen_run_id)
                               VALUES ($1,$2,'aggregate',NULL,$3,$4,$5,$6,$7,$8,$8)
                               ON CONFLICT (finding_type, entity_type, COALESCE(entity_id, '')) WHERE status IN ('OPEN','ACTIONED','REVIEW')
                               DO UPDATE SET severity=EXCLUDED.severity, count=EXCLUDED.count, observed_state=EXCLUDED.observed_state, last_seen_run_id=EXCLUDED.last_seen_run_id""",
                            f["finding_type"], f["severity"], f["count"], f["expected_state"], f["observed_state"], f["recommended_action"], f["auto_action_allowed"], run_id)
                    # findings no longer detected → RESOLVED (superseding state, row kept)
                    await con.execute(
                        "UPDATE housekeeping.findings SET status='RESOLVED', resolved_run_id=$1, resolved_at=now() WHERE status IN ('OPEN','ACTIONED') AND entity_type='aggregate' AND NOT (finding_type = ANY($2::text[]))",
                        run_id, list(seen))
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        async with db.pool.acquire() as con:
            await con.execute("UPDATE housekeeping.runs SET finished_at=now(), checks_run=$2, findings_open=$3, duration_ms=$4, error=$5 WHERE run_id=$1",
                              run_id, len(status.get("reconciliation", {})), len(status.get("findings", [])), int((time.monotonic() - t0) * 1000), error)
    return {"run_id": run_id, "dry_run": dry_run, "checks": len(status["reconciliation"]), "findings_open": len(status["findings"]), "health": status["health"]}


async def list_findings(db: Any, *, ftype: str | None, status: str | None, severity: str | None, limit: int) -> list[dict[str, Any]]:
    async with db.pool.acquire() as con:
        rows = await con.fetch(
            """SELECT finding_id, finding_type, severity, entity_type, entity_id, count, expected_state, observed_state, recommended_action, auto_action_allowed,
                      status, first_seen_run_id, last_seen_run_id, resolved_run_id, resolved_at, first_seen_at, sample
               FROM housekeeping.findings
               WHERE ($1::text IS NULL OR finding_type=$1) AND ($2::text IS NULL OR status=$2) AND ($3::text IS NULL OR severity=$3)
               ORDER BY CASE severity WHEN 'CRITICAL' THEN 0 WHEN 'ERROR' THEN 1 WHEN 'WARN' THEN 2 ELSE 3 END, count DESC, finding_id DESC LIMIT $4""",
            ftype, status or "OPEN" if status != "ALL" else None, severity, limit)
    out = []
    for r in rows:
        d = dict(r)
        d["sample"] = json.loads(d["sample"]) if isinstance(d["sample"], str) else d["sample"]
        out.append(d)
    return out


async def last_run(db: Any) -> dict[str, Any] | None:
    async with db.pool.acquire() as con:
        r = await con.fetchrow("SELECT run_id, trigger, started_at, finished_at, dry_run, checks_run, findings_open, actions_taken, duration_ms, error FROM housekeeping.runs ORDER BY run_id DESC LIMIT 1")
    return dict(r) if r else None
