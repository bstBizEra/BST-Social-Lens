"""HK-001 §6–§8, read-only: per-stage watermarks, pairwise reconciliation ratios, aggregate findings.

Nothing here writes. Every number is computed from the run tables and row timestamps that the 001 pipeline
already keeps (lineage sources, §5); stages whose schema is absent (resolution behind the flag) report
`enabled: false` instead of failing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

HK_VERSION = "0.1.0"
STAGES = ("capture", "raw", "ingest", "extract", "geo", "resolve", "snapshot", "publish", "retention")
LIFECYCLE_DAYS = ((30, "CURRENT"), (90, "AGING"), (180, "STALE"))


def classify_health(ratio: float | None) -> str:
    """HK-001 §7 roll-up: healthy ≥ 0.99, degraded ≥ 0.9, failing below; unknown when nothing to reconcile."""
    if ratio is None:
        return "unknown"
    if ratio >= 0.99:
        return "healthy"
    if ratio >= 0.9:
        return "degraded"
    return "failing"


def lifecycle_state(last_observed: datetime | None, now: datetime | None = None) -> str:
    """HK-001 §10 starting policy: CURRENT 0–30 d, AGING 31–90, STALE 91–180, HISTORICAL beyond."""
    if last_observed is None:
        return "UNKNOWN"
    now = now or datetime.now(timezone.utc)
    age = (now - last_observed).days
    for limit, state in LIFECYCLE_DAYS:
        if age <= limit:
            return state
    return "HISTORICAL"


def _finding(ftype: str, severity: str, count: int, expected: str, observed: str, action: str, auto: bool) -> dict[str, Any]:
    return {"finding_type": ftype, "severity": severity, "count": count, "expected_state": expected, "observed_state": observed,
            "recommended_action": action, "auto_action_allowed": auto, "status": "OPEN"}


async def housekeeping_status(db: Any, *, rules_version: str, raw_retention_days: int, record_retention_days: int,
                              resolution_enabled: bool, review_sla_days: int = 1) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    findings: list[dict[str, Any]] = []
    recon: dict[str, dict[str, Any]] = {}
    wm: dict[str, dict[str, Any]] = {}

    async with db.pool.acquire() as con:
        # ---- capture / ingest / raw (L0/L1)
        rec = await con.fetchrow("""SELECT count(*) n, max(captured_at) last_capture, max(first_seen) last_ingest,
                                          count(*) FILTER (WHERE first_payload_hash IS NOT NULL) with_hash,
                                          count(*) FILTER (WHERE first_payload_hash IS NOT NULL AND EXISTS (SELECT 1 FROM raw_captures c WHERE c.payload_hash = records.first_payload_hash)) resolved,
                                          count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM capture_events e WHERE e.record_key = records.key)) no_event,
                                          count(*) FILTER (WHERE protected) protected
                                   FROM records""")
        raw = await con.fetchrow("""SELECT count(*) n, count(body) with_body, max(captured_at) last_raw,
                                          count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM records r WHERE r.first_payload_hash = raw_captures.payload_hash OR r.last_payload_hash = raw_captures.payload_hash)
                                                             AND NOT EXISTS (SELECT 1 FROM capture_events e WHERE e.payload_hash = raw_captures.payload_hash)) orphan,
                                          count(*) FILTER (WHERE body IS NOT NULL AND captured_at < $1) body_overdue
                                   FROM raw_captures""", now - timedelta(days=raw_retention_days) if raw_retention_days > 0 else datetime(1970, 1, 1, tzinfo=timezone.utc))
        ingest_run = await con.fetchrow("SELECT max(received_at) last FROM ingest_runs") if await con.fetchval("SELECT to_regclass('ingest_runs') IS NOT NULL") else None
        wm["capture"] = {"last_success_at": rec["last_capture"], "records_in": rec["n"]}
        wm["ingest"] = {"last_success_at": (ingest_run["last"] if ingest_run else None) or rec["last_ingest"], "records_in": rec["n"]}
        wm["raw"] = {"last_success_at": raw["last_raw"], "records_in": raw["n"], "records_pending": rec["with_hash"] - rec["resolved"]}
        r_evid = rec["resolved"] / rec["with_hash"] if rec["with_hash"] else None
        recon["R-EVID"] = {"expected": rec["with_hash"], "observed": rec["resolved"], "ratio": round(r_evid, 4) if r_evid is not None else None}
        if rec["with_hash"] - rec["resolved"] > 0:
            findings.append(_finding("RAW_MISSING", "WARN" if r_evid and r_evid > 0.5 else "ERROR", rec["with_hash"] - rec["resolved"],
                                     "every L1 row resolves to a raw capture (001B)", f"{rec['resolved']}/{rec['with_hash']} resolved",
                                     "MARK_RAW_NEEDED → GET /raw/needed; the extension re-sends bodies it still holds on its next sync (0.7.4+)", True))
        if raw["orphan"]:
            findings.append(_finding("RAW_ORPHAN", "INFO", raw["orphan"], "raw captures referenced by a record or event", f"{raw['orphan']} unreferenced", "keep; parser gap corpus", False))
        recon["R-SIGHT"] = {"expected": rec["n"], "observed": rec["n"] - rec["no_event"], "ratio": round((rec["n"] - rec["no_event"]) / rec["n"], 4) if rec["n"] else None}
        if rec["no_event"]:
            findings.append(_finding("RECORD_WITHOUT_EVENT", "WARN", rec["no_event"], "one capture_events row per sighting", f"{rec['no_event']} records without events", "backfill from records.captured_at", True))

        # ---- extract (L2 stage)
        ext = await con.fetchrow("""SELECT count(*) n_obs, max(observed_at) last_obs,
                                          count(*) FILTER (WHERE method_version <> $1) behind
                                   FROM extract.current_observations""", rules_version)
        pend = await con.fetchrow("""SELECT count(*) n, min(r.captured_at) oldest FROM records r
                                    WHERE NOT EXISTS (SELECT 1 FROM extract.current_observations o WHERE o.record_key = r.key AND o.content_hash = r.content_hash)""")
        run = await con.fetchrow("SELECT max(finished_at) FILTER (WHERE error IS NULL) ok, max(finished_at) FILTER (WHERE error IS NOT NULL) fail FROM extract.runs")
        wm["extract"] = {"last_success_at": run["ok"], "last_failure_at": run["fail"], "records_in": ext["n_obs"], "records_pending": pend["n"],
                         "oldest_pending_at": pend["oldest"], "lag_seconds": int((now - pend["oldest"]).total_seconds()) if pend["oldest"] else 0,
                         "method_version": rules_version, "behind_version": ext["behind"]}
        recon["R-EXTR"] = {"expected": rec["n"], "observed": rec["n"] - pend["n"], "ratio": round((rec["n"] - pend["n"]) / rec["n"], 4) if rec["n"] else None}
        if pend["n"]:
            findings.append(_finding("EXTRACTION_MISSING", "WARN", pend["n"], "every record has a current observation for its content_hash", f"{pend['n']} pending", "extraction loop / POST /admin/extract", True))
        if ext["behind"]:
            findings.append(_finding("EXTRACTION_STALE", "INFO", ext["behind"], f"method_version {rules_version}", f"{ext['behind']} observations on an older version", "re-extract (loop does this)", True))

        # ---- geo
        geo = await con.fetchrow("""SELECT count(*) FILTER (WHERE has_loc) with_claims,
                                          count(*) FILTER (WHERE has_loc AND prec IS NOT NULL) resolved,
                                          count(*) FILTER (WHERE has_loc AND prec IN ('VILLAGE','DISTRICT','PROVINCE','PARCEL_APPROXIMATE','EXACT_COORDINATE')) located,
                                          count(*) FILTER (WHERE conflict) conflicts
                                   FROM (SELECT o.observation_id,
                                                EXISTS (SELECT 1 FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field IN ('LOCATION_TEXT','MAP_URL','COORDINATE')) has_loc,
                                                (SELECT l.precision FROM geo.resolved_locations l WHERE l.observation_id=o.observation_id AND l.is_primary ORDER BY l.resolved_location_id DESC LIMIT 1) prec,
                                                EXISTS (SELECT 1 FROM geo.resolved_locations l WHERE l.observation_id=o.observation_id AND l.signals::text LIKE '%conflict%') conflict
                                         FROM extract.current_observations o) x""")
        gaz = await con.fetchval("SELECT count(*) FROM geo.admin_versions WHERE is_current") if await con.fetchval("SELECT to_regclass('geo.admin_versions') IS NOT NULL") else 0
        wm["geo"] = {"records_in": geo["with_claims"], "gazetteer_loaded": bool(gaz), "located_share": round(geo["located"] / geo["with_claims"], 4) if geo["with_claims"] else None}
        r_geo = geo["resolved"] / geo["with_claims"] if geo["with_claims"] else None
        recon["R-GEO"] = {"expected": geo["with_claims"], "observed": geo["resolved"], "ratio": round(r_geo, 4) if r_geo is not None else None,
                          "note": "resolution row present (any precision); located_share is the 001D quality signal"}
        if geo["with_claims"] and geo["located"] == 0:
            findings.append(_finding("GEO_UNRESOLVED", "WARN", geo["with_claims"] - geo["located"], "locations resolved to admin units",
                                     "all TEXT_ONLY" if not gaz else f"{geo['located']}/{geo['with_claims']} located", "import the Lao Data Map gazetteer (001D D4)" if not gaz else "review location queue", False))
        if geo["conflicts"]:
            findings.append(_finding("GEO_CONFLICT", "WARN", geo["conflicts"], "text and point agree", f"{geo['conflicts']} conflicts", "location review queue", False))

        # ---- resolve / snapshot (flag)
        if resolution_enabled and await con.fetchval("SELECT to_regclass('resolution.current_decisions') IS NOT NULL"):
            res = await con.fetchrow("""SELECT count(*) FILTER (WHERE d.observation_id IS NULL) missing,
                                              count(*) FILTER (WHERE d.decision='REVIEW_REQUIRED' AND d.created_at < $1) ambiguous_old,
                                              count(*) n
                                       FROM extract.current_observations o LEFT JOIN resolution.current_decisions d USING (observation_id)
                                       WHERE o.signal_class IN ('PROPERTY_SALE','PROPERTY_RENT')""", now - timedelta(days=review_sla_days))
            rrun = await con.fetchrow("SELECT max(finished_at) FILTER (WHERE error IS NULL) ok, max(finished_at) FILTER (WHERE error IS NOT NULL) fail FROM resolution.runs")
            props = await con.fetchrow("""SELECT count(*) n,
                                                count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM resolution.current_decisions d WHERE d.market_property_id=p.market_property_id AND d.decision NOT IN ('REJECTED','UNLINKED'))) empty,
                                                count(*) FILTER (WHERE (SELECT max(s.computed_at) FROM market.property_stats_snapshots s WHERE s.market_property_id=p.market_property_id)
                                                                  < (SELECT max(o.observed_at) FROM resolution.current_decisions d JOIN extract.observations o USING (observation_id) WHERE d.market_property_id=p.market_property_id)) stale_snap
                                         FROM market.properties p WHERE p.status='ACTIVE'""")
            life = await con.fetch("""SELECT p.market_property_id, max(o.observed_at) last_obs FROM market.properties p
                                      JOIN resolution.current_decisions d ON d.market_property_id=p.market_property_id JOIN extract.observations o USING (observation_id)
                                      WHERE p.status='ACTIVE' GROUP BY 1""")
            states: dict[str, int] = {}
            for r in life:
                s = lifecycle_state(r["last_obs"], now)
                states[s] = states.get(s, 0) + 1
            wm["resolve"] = {"last_success_at": rrun["ok"], "last_failure_at": rrun["fail"], "records_in": res["n"], "records_pending": res["missing"], "enabled": True}
            wm["snapshot"] = {"records_in": props["n"], "records_pending": props["stale_snap"], "lifecycle": states}
            recon["R-RES"] = {"expected": res["n"], "observed": res["n"] - res["missing"], "ratio": round((res["n"] - res["missing"]) / res["n"], 4) if res["n"] else None}
            recon["R-PROP"] = {"expected": props["n"], "observed": props["n"] - props["empty"], "ratio": round((props["n"] - props["empty"]) / props["n"], 4) if props["n"] else None}
            recon["R-SNAP"] = {"expected": props["n"], "observed": props["n"] - props["stale_snap"], "ratio": round((props["n"] - props["stale_snap"]) / props["n"], 4) if props["n"] else None}
            if res["missing"]:
                findings.append(_finding("MATCH_MISSING", "WARN", res["missing"], "every SALE/RENT observation has a decision", f"{res['missing']} undecided", "POST /admin/resolve", True))
            if res["ambiguous_old"]:
                findings.append(_finding("MATCH_AMBIGUOUS", "WARN", res["ambiguous_old"], f"review within {review_sla_days} d", f"{res['ambiguous_old']} REVIEW_REQUIRED older than SLA", "match review queue (review.py)", False))
            if props["empty"]:
                findings.append(_finding("PROPERTY_EMPTY", "ERROR", props["empty"], "ACTIVE property has ≥1 current observation", f"{props['empty']} empty", "review: merge/supersede", False))
            if props["stale_snap"]:
                findings.append(_finding("SNAPSHOT_STALE", "INFO", props["stale_snap"], "snapshot newer than latest observation", f"{props['stale_snap']} stale", "POST /admin/quality/recompute", True))
        else:
            wm["resolve"] = {"enabled": False}
            wm["snapshot"] = {"enabled": False}
            recon["R-RES"] = {"expected": None, "observed": None, "ratio": None, "note": "resolution disabled (LENS_RESOLUTION_ENABLED=0)"}

        # ---- publish (L3): schema only with the flag; report enabled=false otherwise
        pub_on = await con.fetchval("SELECT to_regclass('publish.dataset_versions') IS NOT NULL")
        if pub_on:
            pub = await con.fetchrow("SELECT count(*) FILTER (WHERE status='PUBLISHED') published, max(published_at) last FROM publish.dataset_versions")
            wm["publish"] = {"enabled": True, "records_in": pub["published"], "last_success_at": pub["last"]}
        else:
            wm["publish"] = {"enabled": False}

        # ---- retention
        rec_overdue = await con.fetchval("""SELECT count(*) FROM records WHERE NOT protected AND COALESCE(created_at, captured_at) < $1""",
                                         now - timedelta(days=record_retention_days)) if record_retention_days > 0 else 0
        wm["retention"] = {"raw_body_days": raw_retention_days, "record_days": record_retention_days, "records_pending": raw["body_overdue"] + rec_overdue, "protected": rec["protected"]}
        recon["R-RET"] = {"expected": 0, "observed": raw["body_overdue"] + rec_overdue, "ratio": 1.0 if (raw["body_overdue"] + rec_overdue) == 0 else 0.0}
        if raw["body_overdue"] + rec_overdue:
            findings.append(_finding("RETENTION_OVERDUE", "WARN", raw["body_overdue"] + rec_overdue, "nothing past policy", f"raw bodies {raw['body_overdue']}, records {rec_overdue}", "POST /admin/purge (daily loop)", True))

    # ---- stage health roll-up
    health = {
        "capture": classify_health(recon["R-SIGHT"]["ratio"]),
        "raw": classify_health(recon["R-EVID"]["ratio"]),
        "extract": classify_health(recon["R-EXTR"]["ratio"]),
        "geo": classify_health(recon["R-GEO"]["ratio"]),
        "resolve": classify_health(recon["R-RES"]["ratio"]) if recon["R-RES"]["ratio"] is not None else "disabled",
        "retention": classify_health(recon["R-RET"]["ratio"]),
    }
    sev_rank = {"INFO": 0, "WARN": 1, "ERROR": 2, "CRITICAL": 3}
    findings.sort(key=lambda f: (-sev_rank[f["severity"]], -f["count"]))
    return {"hk_version": HK_VERSION, "computed_at": now, "watermarks": wm, "reconciliation": recon, "health": health,
            "findings": findings, "open_findings": len(findings)}
