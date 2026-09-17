"""Persistence for extraction output (001C §3, §8) against the `extract.*` schema.

Duck-typed on an asyncpg pool (`db.pool`). Every write is append-only; no statement here
touches L0/L1 tables except SELECT on `records` (schema-lint keeps DDL honest; this module
keeps DML honest — reviewers: grep for UPDATE/DELETE, there are none on L1).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..quality.dq import DQ_VERSION, DqInput, evaluate_rules, score_observation
from .models import Observation

_SELECT_CANDIDATES = """
SELECT r.key, r.record_type, r.text, r.author_name, r.created_at, r.captured_at, r.content_hash
FROM records r
LEFT JOIN extract.current_observations o ON o.record_key = r.key
WHERE ($1::timestamptz IS NULL OR r.captured_at >= $1)
  AND (
        $3::boolean
     OR o.observation_id IS NULL
     OR o.content_hash IS DISTINCT FROM r.content_hash
     OR o.method_version <> $4
  )
ORDER BY r.captured_at DESC
LIMIT $2
"""


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class ExtractStore:
    def __init__(self, db: Any, contact_key: str | None = None) -> None:
        self.db = db
        self.contact_key = contact_key or None

    # ---------------------------------------------------------------- runs

    async def start_run(self, trigger: str, method_set: str, method_version: str, kg_version: str) -> int:
        async with self.db.pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO extract.runs (trigger, method_set, method_version, keyword_groups_version) VALUES ($1,$2,$3,$4) RETURNING run_id",
                trigger, method_set, method_version, kg_version,
            )

    async def finish_run(self, run_id: int, records_in: int, observations_out: int, claims_out: int, error: str | None = None) -> None:
        async with self.db.pool.acquire() as con:
            await con.execute(
                "UPDATE extract.runs SET finished_at = now(), records_in=$2, observations_out=$3, claims_out=$4, error=$5 WHERE run_id=$1",
                run_id, records_in, observations_out, claims_out, error,
            )

    # ---------------------------------------------------------------- candidates + FX

    async def candidates(self, since: datetime | None, limit: int, force: bool, method_version: str) -> list[dict[str, Any]]:
        async with self.db.pool.acquire() as con:
            rows = await con.fetch(_SELECT_CANDIDATES, since, limit, force, method_version)
        return [dict(r) for r in rows]

    async def fx_lookup(self, currency: str, date_iso: str | None) -> tuple[Decimal, str, str] | None:
        """Rate at the post date, else the nearest earlier rate within 7 days (001C §6)."""
        d = date.fromisoformat(date_iso[:10]) if date_iso else datetime.now(timezone.utc).date()
        async with self.db.pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT lak_per_unit, rate_date, source FROM extract.fx_rates WHERE currency=$1 AND rate_date <= $2 AND rate_date >= $3 ORDER BY rate_date DESC LIMIT 1",
                currency, d, d - timedelta(days=7),
            )
        return (Decimal(row["lak_per_unit"]), row["rate_date"].isoformat(), row["source"]) if row else None

    async def upsert_fx(self, currency: str, rate_date: date, lak_per_unit: Decimal, source: str) -> None:
        async with self.db.pool.acquire() as con:
            await con.execute(
                "INSERT INTO extract.fx_rates (currency, rate_date, lak_per_unit, source) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (currency, rate_date) DO UPDATE SET lak_per_unit = EXCLUDED.lak_per_unit, source = EXCLUDED.source, loaded_at = now()",
                currency, rate_date, lak_per_unit, source,
            )

    # ---------------------------------------------------------------- write one observation

    async def insert_observation(self, run_id: int, rec: dict[str, Any], obs: Observation, pgcrypto: bool,
                                 resolutions: list[Any] | None = None, geo: Any = None, admin_version: str | None = None) -> tuple[int, int]:
        """Insert observation + claims + price observations + contact points (+ resolved locations, 001D) in one transaction.
        Returns (observation_id, claims)."""
        async with self.db.pool.acquire() as con:
            async with con.transaction():
                oid = await con.fetchval(
                    """INSERT INTO extract.observations
                       (record_key, run_id, content_hash, record_type, signal_class, signal_confidence, asset_type, asset_confidence,
                        extraction_method, method_version, keyword_groups_version, observed_at, post_date, signals, group_hits)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb) RETURNING observation_id""",
                    rec["key"], run_id, rec.get("content_hash"), rec.get("record_type") or "post",
                    obs.signal_class, obs.signal_confidence, obs.asset_type, obs.asset_confidence,
                    obs.extraction_method, obs.method_version, obs.keyword_groups_version,
                    rec["captured_at"], rec.get("created_at"), _j(obs.signals), _j(obs.group_hits),
                )
                claim_ids: list[int] = []
                for c in obs.claims:
                    normalised = {k: v for k, v in c.normalised.items() if k != "raw_value"}
                    cid = await con.fetchval(
                        """INSERT INTO extract.claims
                           (observation_id, field, value_text, span_start, span_end, extraction_method, method_version, confidence,
                            review_status, normalised, normalisation_status, signals)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12::jsonb) RETURNING claim_id""",
                        oid, c.field, c.value_text, c.evidence_span.start, c.evidence_span.end, c.extraction_method, c.method_version,
                        c.confidence, c.review_status, _j({k: (str(v) if isinstance(v, Decimal) else v) for k, v in normalised.items()}),
                        c.normalisation_status, _j(c.signals),
                    )
                    claim_ids.append(cid)
                    if c.field == "CONTACT":
                        raw = c.normalised.get("raw_value")
                        h = c.normalised["contact_hash"]
                        if pgcrypto and self.contact_key and raw:
                            await con.execute(
                                """INSERT INTO extract.contact_points (contact_hash, kind, masked_value, raw_value_enc)
                                   VALUES ($1,$2,$3, pgp_sym_encrypt($4,$5))
                                   ON CONFLICT (contact_hash) DO UPDATE SET last_seen = now(), sightings = extract.contact_points.sightings + 1,
                                     raw_value_enc = COALESCE(extract.contact_points.raw_value_enc, EXCLUDED.raw_value_enc)""",
                                h, c.normalised["kind"], c.normalised["masked_value"], raw, self.contact_key,
                            )
                        else:
                            await con.execute(
                                """INSERT INTO extract.contact_points (contact_hash, kind, masked_value)
                                   VALUES ($1,$2,$3)
                                   ON CONFLICT (contact_hash) DO UPDATE SET last_seen = now(), sightings = extract.contact_points.sightings + 1""",
                                h, c.normalised["kind"], c.normalised["masked_value"],
                            )
                        await con.execute(
                            "INSERT INTO extract.contact_sightings (claim_id, observation_id, contact_hash) VALUES ($1,$2,$3)", cid, oid, h,
                        )
                for p in obs.price_observations:
                    await con.execute(
                        """INSERT INTO extract.price_observations
                           (observation_id, claim_id, price_type, amount_original, currency_original, price_basis, amount_lak, fx_rate,
                            fx_rate_date, fx_source, price_per_sqm_lak, observed_at, post_date, confidence)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
                        oid, claim_ids[p.claim_index], p.price_type, p.amount_original, p.currency_original, p.price_basis,
                        p.amount_lak, p.fx_rate, date.fromisoformat(p.fx_rate_date[:10]) if p.fx_rate_date else None, p.fx_source,
                        p.price_per_sqm_lak, rec["captured_at"], rec.get("created_at"), p.confidence,
                    )
                if resolutions and geo is not None:
                    await geo.insert_resolutions(con, oid, run_id, claim_ids, resolutions, admin_version)
        return oid, len(claim_ids)

    # ---------------------------------------------------------------- reads

    async def get_observation(self, key: str, all_runs: bool = False) -> dict[str, Any] | None:
        async with self.db.pool.acquire() as con:
            if all_runs:
                obs = await con.fetch("SELECT * FROM extract.observations WHERE record_key=$1 ORDER BY run_id DESC", key)
            else:
                obs = await con.fetch("SELECT * FROM extract.current_observations WHERE record_key=$1", key)
            if not obs:
                return None
            out = []
            for o in obs:
                claims = await con.fetch("SELECT * FROM extract.claims WHERE observation_id=$1 ORDER BY claim_id", o["observation_id"])
                prices = await con.fetch("SELECT * FROM extract.price_observations WHERE observation_id=$1 ORDER BY price_observation_id", o["observation_id"])
                locs = await con.fetch("SELECT * FROM geo.resolved_locations WHERE observation_id=$1 ORDER BY is_primary DESC, confidence DESC", o["observation_id"])
                out.append({**_row(o), "claims": [_row(c) for c in claims], "price_observations": [_row(p) for p in prices], "locations": [_row(loc) for loc in locs]})
        return out[0] if not all_runs else {"record_key": key, "observations": out}

    async def list_observations(self, signal_class: str | None, asset_type: str | None, since: datetime | None, min_conf: float, limit: int) -> list[dict[str, Any]]:
        async with self.db.pool.acquire() as con:
            rows = await con.fetch(
                """SELECT observation_id, record_key, run_id, signal_class, signal_confidence, asset_type, asset_confidence, observed_at, post_date
                   FROM extract.current_observations
                   WHERE ($1::text IS NULL OR signal_class = $1) AND ($2::text IS NULL OR asset_type = $2)
                     AND ($3::timestamptz IS NULL OR observed_at >= $3) AND signal_confidence >= $4
                   ORDER BY observed_at DESC LIMIT $5""",
                signal_class, asset_type, since, min_conf, limit,
            )
        return [_row(r) for r in rows]

    async def stats(self) -> dict[str, Any]:
        async with self.db.pool.acquire() as con:
            by_class = await con.fetch("SELECT signal_class, count(*) AS n FROM extract.current_observations GROUP BY 1 ORDER BY 2 DESC")
            by_asset = await con.fetch("SELECT asset_type, count(*) AS n FROM extract.current_observations GROUP BY 1 ORDER BY 2 DESC")
            totals = await con.fetchrow(
                """SELECT (SELECT count(*) FROM extract.current_observations) AS observations,
                          (SELECT count(*) FROM extract.claims) AS claims,
                          (SELECT count(*) FROM extract.claims WHERE confidence IS NULL) AS claims_without_confidence,
                          (SELECT count(*) FROM extract.claims WHERE review_status = 'LOW_CONFIDENCE') AS low_confidence_claims,
                          (SELECT count(*) FROM extract.price_observations) AS price_observations,
                          (SELECT count(*) FROM extract.price_observations WHERE amount_lak IS NULL) AS price_observations_no_fx,
                          (SELECT count(*) FROM extract.contact_points) AS contact_points,
                          (SELECT count(*) FROM records) AS records,
                          (SELECT count(*) FROM records r WHERE NOT EXISTS (SELECT 1 FROM extract.current_observations o WHERE o.record_key = r.key)) AS records_unobserved"""
            )
            runs = await con.fetch("SELECT run_id, started_at, finished_at, trigger, method_set, method_version, records_in, observations_out, claims_out, retired, error FROM extract.runs ORDER BY run_id DESC LIMIT 10")
        t = dict(totals)
        n = t["observations"] or 0
        unc = next((r["n"] for r in by_class if r["signal_class"] == "UNCERTAIN"), 0)
        return {
            **t,
            "uncertain_share": round(unc / n, 4) if n else None,
            "by_signal_class": {r["signal_class"]: r["n"] for r in by_class},
            "by_asset_type": {r["asset_type"]: r["n"] for r in by_asset},
            "runs": [_row(r) for r in runs],
        }


# ---------------------------------------------------------------- quality (001G §2), read-only until 001F snapshots exist

_DQ_VIEW = """
SELECT o.observation_id, o.record_key, o.post_date,
       (SELECT c.confidence FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='PRICE' ORDER BY c.confidence DESC LIMIT 1) AS price_conf,
       EXISTS (SELECT 1 FROM extract.price_observations p WHERE p.observation_id=o.observation_id AND p.amount_lak IS NOT NULL) AS price_lak,
       EXISTS (SELECT 1 FROM extract.price_observations p WHERE p.observation_id=o.observation_id) AS has_price_obs,
       EXISTS (SELECT 1 FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='PRICE') AS has_price_claim,
       (SELECT c.confidence FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='AREA' ORDER BY c.confidence DESC LIMIT 1) AS area_conf,
       (SELECT (c.normalised->>'area_sqm')::numeric FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='AREA' ORDER BY c.confidence DESC LIMIT 1) AS area_sqm,
       (SELECT p.amount_lak FROM extract.price_observations p WHERE p.observation_id=o.observation_id ORDER BY p.confidence DESC LIMIT 1) AS amount_lak,
       (SELECT p.price_per_sqm_lak FROM extract.price_observations p WHERE p.observation_id=o.observation_id AND p.price_per_sqm_lak IS NOT NULL LIMIT 1) AS psqm,
       EXISTS (SELECT 1 FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field IN ('LOCATION_TEXT','MAP_URL','COORDINATE')) AS has_loc_claims,
       (SELECT l.precision FROM geo.resolved_locations l WHERE l.observation_id=o.observation_id AND l.is_primary ORDER BY l.resolved_location_id DESC LIMIT 1) AS precision,
       r.first_payload_hash, (rc.payload_hash IS NOT NULL) AS raw_row, (rc.body IS NOT NULL) AS raw_body
FROM extract.current_observations o
LEFT JOIN records r ON r.key = o.record_key
LEFT JOIN raw_captures rc ON rc.payload_hash = r.first_payload_hash
WHERE o.signal_class IN ('PROPERTY_SALE','PROPERTY_RENT','PROPERTY_WANTED')
"""


def _dq_input(r: Any, decision: str | None) -> DqInput:
    return DqInput(
        price_present=r["price_conf"] is not None, price_confidence=float(r["price_conf"] or 0), price_has_lak=bool(r["price_lak"]),
        area_present=r["area_conf"] is not None, area_confidence=float(r["area_conf"] or 0),
        location_precision=r["precision"] or ("TEXT_ONLY" if r["has_loc_claims"] else "UNKNOWN"),
        post_date_present=r["post_date"] is not None, payload_hash_present=bool(r["first_payload_hash"]),
        raw_row_present=bool(r["raw_row"]), raw_body_present=bool(r["raw_body"]), decision=decision,
    )


async def _current_decisions(con: Any, obs_ids: list[int] | None) -> dict[int, str]:
    """observation_id → current 001E decision, when resolution.* exists (flag on); {} otherwise."""
    if not await con.fetchval("SELECT to_regclass('resolution.current_decisions') IS NOT NULL"):
        return {}
    if obs_ids is None:
        rows = await con.fetch("SELECT observation_id, decision FROM resolution.current_decisions")
    else:
        rows = await con.fetch("SELECT observation_id, decision FROM resolution.current_decisions WHERE observation_id = ANY($1::bigint[])", obs_ids)
    return {r["observation_id"]: r["decision"] for r in rows}


async def dq_for_observations(con: Any, obs_ids: list[int] | None = None) -> dict[int, Any]:
    """observation_id → DqResult for current property observations (all, or the given ids). Entity-match component
    uses the current 001E decision when resolution is enabled, singleton credit otherwise (001G §2)."""
    sql = _DQ_VIEW + (" AND o.observation_id = ANY($1::bigint[])" if obs_ids is not None else "")
    rows = await con.fetch(sql, obs_ids) if obs_ids is not None else await con.fetch(sql)
    decisions = await _current_decisions(con, obs_ids)
    return {r["observation_id"]: score_observation(_dq_input(r, decisions.get(r["observation_id"]))) for r in rows}


async def quality_stats(db: Any) -> dict[str, Any]:
    """DQ distribution + validation exceptions over current property observations (computed on read; property grades live on snapshots)."""
    async with db.pool.acquire() as con:
        rows = await con.fetch(_DQ_VIEW)
        decisions = await _current_decisions(con, None)
    by_grade: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
    total = 0
    exceptions: dict[str, int] = {}
    low: list[dict[str, Any]] = []
    for r in rows:
        res = score_observation(_dq_input(r, decisions.get(r["observation_id"])))
        by_grade[res.grade] += 1
        total += res.score
        for e in evaluate_rules({"amount_lak": r["amount_lak"], "area_sqm": r["area_sqm"], "price_per_sqm_lak": r["psqm"],
                                 "post_date": r["post_date"].date() if r["post_date"] else None,
                                 "has_price_observation": r["has_price_obs"], "has_price_claim": r["has_price_claim"],
                                 "has_location_claims": r["has_loc_claims"], "has_resolution": r["precision"] is not None}):
            exceptions[e.rule] = exceptions.get(e.rule, 0) + 1
        if res.grade == "D" and len(low) < 20:
            low.append({"observation_id": r["observation_id"], "record_key": r["record_key"], "score": res.score})
    n = len(rows)
    return {"dq_version": DQ_VERSION, "observations": n, "by_grade": by_grade, "mean_score": round(total / n, 1) if n else None,
            "share_b_or_better": round((by_grade["A"] + by_grade["B"]) / n, 4) if n else None, "exceptions": exceptions,
            "lowest": low, "entity_match_source": "resolution.current_decisions" if decisions else "singleton credit (resolution disabled or no decisions)",
            "note": "computed on read over current property observations; property grades are on market.property_stats_snapshots.dq_grade"}


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, Decimal):
            d[k] = format(v.normalize(), 'f') if v == v.to_integral() else format(v, 'f')
        elif isinstance(v, float):
            d[k] = round(v, 4)  # REAL columns come back as float32 noise (0.800000011920929)
        elif isinstance(v, str) and k in ("normalised", "signals", "group_hits"):
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    return d
