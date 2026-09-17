"""Persistence for entity resolution (001E §7–§9) on the 001F draft schema (`resolution.*`, `market.*`).

Only active when `db.enable_market` is true (LENS_RESOLUTION_ENABLED=1) — the schema stays unapplied in
production until 001E freezes (ADR-0005 §2.4). Everything is append-only: clusters, candidates and decisions
are inserted; market properties change only by status; statistics are snapshots.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Any

from .cluster import Cluster, ClusterInput
from .match import Score, Side

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """26-char Crockford ULID (time48 + random80), sortable, CHECK-compatible with 001F."""
    t = int(time.time() * 1000)
    r = int.from_bytes(secrets.token_bytes(10), "big")
    n = (t << 80) | r
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(out))


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


_SIDES = """
SELECT o.observation_id, o.record_key, o.signal_class, o.asset_type, o.asset_confidence, o.post_date, r.text, r.author_hash,
       l.precision, l.lat, l.lng, l.village_code, l.district_code,
       (SELECT c.normalised->>'url' FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='MAP_URL' ORDER BY c.claim_id LIMIT 1) AS map_url,
       (SELECT (c.normalised->>'area_sqm')::numeric FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='AREA' ORDER BY c.confidence DESC LIMIT 1) AS area_sqm,
       (SELECT c.confidence FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='AREA' ORDER BY c.confidence DESC LIMIT 1) AS area_conf,
       (SELECT (c.normalised->>'metres')::numeric FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='FRONTAGE' LIMIT 1) AS frontage_m,
       (SELECT (c.normalised->>'metres')::numeric FROM extract.claims c WHERE c.observation_id=o.observation_id AND c.field='DEPTH' LIMIT 1) AS depth_m,
       (SELECT p.price_type FROM extract.price_observations p WHERE p.observation_id=o.observation_id ORDER BY p.confidence DESC LIMIT 1) AS price_type,
       (SELECT p.amount_lak FROM extract.price_observations p WHERE p.observation_id=o.observation_id ORDER BY p.confidence DESC LIMIT 1) AS amount_lak,
       (SELECT p.amount_original FROM extract.price_observations p WHERE p.observation_id=o.observation_id ORDER BY p.confidence DESC LIMIT 1) AS amount_original,
       (SELECT p.currency_original FROM extract.price_observations p WHERE p.observation_id=o.observation_id ORDER BY p.confidence DESC LIMIT 1) AS currency,
       COALESCE((SELECT array_agg(DISTINCT s.contact_hash) FROM extract.contact_sightings s WHERE s.observation_id=o.observation_id), '{}') AS contact_hashes,
       (SELECT cm.cluster_id FROM resolution.cluster_members cm JOIN resolution.listing_clusters lc ON lc.cluster_id=cm.cluster_id AND lc.status='ACTIVE' WHERE cm.record_key=o.record_key LIMIT 1) AS cluster_id,
       d.market_property_id AS current_mp, d.decision AS current_decision, d.source AS current_source
FROM extract.current_observations o
LEFT JOIN records r ON r.key = o.record_key
LEFT JOIN LATERAL (SELECT precision, lat, lng, village_code, district_code FROM geo.resolved_locations x WHERE x.observation_id=o.observation_id AND x.is_primary ORDER BY resolved_location_id DESC LIMIT 1) l ON true
LEFT JOIN resolution.current_decisions d ON d.observation_id = o.observation_id
WHERE o.signal_class IN ('PROPERTY_SALE','PROPERTY_RENT')
ORDER BY o.observation_id
"""


class ResolutionStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ---------------------------------------------------------------- runs

    async def start_run(self, trigger: str, versions: dict[str, str]) -> int:
        async with self.db.pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO resolution.runs (trigger, permalink_rules_version, cluster_version, blocking_version, match_version) VALUES ($1,$2,$3,$4,$5) RETURNING run_id",
                trigger, versions["permalink"], versions["cluster"], versions["blocking"], versions["match"])

    async def finish_run(self, run_id: int, counts: dict[str, int], error: str | None) -> None:
        async with self.db.pool.acquire() as con:
            await con.execute("UPDATE resolution.runs SET finished_at=now(), records_in=$2, clusters_out=$3, candidates_out=$4, decisions_out=$5, error=$6 WHERE run_id=$1",
                              run_id, counts.get("records_in", 0), counts.get("clusters", 0), counts.get("candidates", 0), counts.get("decisions", 0), error)

    # ---------------------------------------------------------------- load

    async def load(self) -> list[dict[str, Any]]:
        async with self.db.pool.acquire() as con:
            rows = await con.fetch(_SIDES)
        return [dict(r) for r in rows]

    @staticmethod
    def to_side(r: dict[str, Any]) -> Side:
        return Side(
            observation_id=r["observation_id"], signal_class=r["signal_class"], asset_type=r["asset_type"] or "UNKNOWN",
            asset_confidence=float(r["asset_confidence"] or 0), precision=r["precision"] or "UNKNOWN",
            lat=r["lat"], lng=r["lng"], village_code=r["village_code"], district_code=r["district_code"], map_url=r["map_url"],
            area_sqm=Decimal(str(r["area_sqm"])) if r["area_sqm"] is not None else None, area_confidence=float(r["area_conf"] or 0),
            frontage_m=Decimal(str(r["frontage_m"])) if r["frontage_m"] is not None else None,
            depth_m=Decimal(str(r["depth_m"])) if r["depth_m"] is not None else None,
            price_type=r["price_type"], amount_lak=Decimal(str(r["amount_lak"])) if r["amount_lak"] is not None else None,
            contact_hashes=frozenset(r["contact_hashes"] or ()), text=r["text"],
            post_date_ordinal=r["post_date"].date().toordinal() if isinstance(r["post_date"], datetime) else None,
            cluster_id=r["cluster_id"],
        )

    @staticmethod
    def to_cluster_input(r: dict[str, Any]) -> ClusterInput:
        return ClusterInput(record_key=r["record_key"], text=r["text"], contact_hashes=frozenset(r["contact_hashes"] or ()),
                            author_hash=r["author_hash"], amount_original=Decimal(str(r["amount_original"])) if r["amount_original"] is not None else None,
                            currency=r["currency"], village_code=r["village_code"], precision=r["precision"] or "UNKNOWN", lat=r["lat"], lng=r["lng"])

    # ---------------------------------------------------------------- write

    async def write_clusters(self, run_id: int, clusters: list[Cluster]) -> int:
        n = 0
        async with self.db.pool.acquire() as con:
            async with con.transaction():
                for c in clusters:
                    exists = await con.fetchval("SELECT 1 FROM resolution.listing_clusters WHERE cluster_id=$1", c.cluster_id)
                    if exists:
                        # append new members only (a cluster only grows; splits are human, 001E §4)
                        for m in c.members:
                            await con.execute("INSERT INTO resolution.cluster_members (cluster_id, record_key) VALUES ($1,$2) ON CONFLICT DO NOTHING", c.cluster_id, m)
                        continue
                    await con.execute("INSERT INTO resolution.listing_clusters (cluster_id, cluster_version, run_id) VALUES ($1,$2,$3)", c.cluster_id, c.cluster_version, run_id)
                    for m in c.members:
                        await con.execute("INSERT INTO resolution.cluster_members (cluster_id, record_key) VALUES ($1,$2) ON CONFLICT DO NOTHING", c.cluster_id, m)
                    for e in c.edges:
                        a, b = sorted((e.a, e.b))
                        await con.execute("INSERT INTO resolution.cluster_edges (cluster_id, record_a, record_b, rule, signals) VALUES ($1,$2,$3,$4,$5::jsonb)", c.cluster_id, a, b, e.rule, _j(e.signals))
                    n += 1
        return n

    async def new_property(self, con: Any, obs_id: int, run_id: int, asset_type: str) -> str:
        mp = "MP-" + ulid()
        await con.execute("INSERT INTO market.properties (market_property_id, created_from_observation_id, created_by_run_id, asset_type) VALUES ($1,$2,$3,$4)",
                          mp, obs_id, run_id, asset_type if asset_type in ("LAND", "HOUSE", "APARTMENT", "COMMERCIAL", "WAREHOUSE", "HOTEL", "FARM", "DEVELOPMENT_LAND", "BUILDING", "OTHER") else "UNKNOWN")
        return mp

    async def write_candidate(self, con: Any, run_id: int, obs_id: int, peer: int | None, mp: str | None, score: Score, reasons: list[str], versions: dict[str, str]) -> int | None:
        return await con.fetchval(
            """INSERT INTO resolution.entity_candidates (run_id, observation_id, peer_observation_id, market_property_id, score, proposed_decision, forced_rule, signals, blocking_reasons, match_version, blocking_version)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11)
               ON CONFLICT DO NOTHING RETURNING candidate_id""",
            run_id, obs_id, peer, mp, score.total, score.decision, score.forced, _j([s.__dict__ for s in score.signals]), _j(reasons), versions["match"], versions["blocking"])

    async def write_decision(self, con: Any, run_id: int, obs_id: int, mp: str, decision: str, source: str, candidate_id: int | None, supersedes: int | None) -> int:
        return await con.fetchval(
            "INSERT INTO resolution.entity_decisions (observation_id, market_property_id, decision, source, candidate_id, supersedes_decision_id, run_id) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING decision_id",
            obs_id, mp, decision, source, candidate_id, supersedes, run_id)

    async def current_decision_id(self, con: Any, obs_id: int) -> int | None:
        return await con.fetchval("SELECT decision_id FROM resolution.current_decisions WHERE observation_id=$1", obs_id)

    # ---------------------------------------------------------------- statistics snapshots (001E §9)

    async def snapshot_stats(self, con: Any, mp: str, stats_version: str = "1.0.0") -> None:
        rows = await con.fetch(
            """SELECT d.observation_id, d.decision, d.source, o.observed_at, o.record_key, cd.score
               FROM resolution.current_decisions d
               JOIN extract.observations o ON o.observation_id = d.observation_id
               LEFT JOIN resolution.entity_candidates cd ON cd.candidate_id = d.candidate_id
               WHERE d.market_property_id=$1 AND d.decision IN ('HIGH_CONFIDENCE_MATCH','REVIEW_REQUIRED','SEPARATE_CANDIDATE','CONFIRMED')""", mp)
        if not rows:
            return
        obs_ids = [r["observation_id"] for r in rows]
        prices = await con.fetch("SELECT price_type, amount_lak, price_per_sqm_lak, observed_at FROM extract.price_observations WHERE observation_id = ANY($1::bigint[]) AND amount_lak IS NOT NULL", obs_ids)
        adv = await con.fetchval("SELECT count(DISTINCT r.author_hash) FROM extract.observations o JOIN records r ON r.key=o.record_key WHERE o.observation_id = ANY($1::bigint[]) AND r.author_hash IS NOT NULL", obs_ids)
        clusters = await con.fetchval("SELECT count(DISTINCT cm.cluster_id) FROM resolution.cluster_members cm JOIN extract.observations o ON o.record_key=cm.record_key WHERE o.observation_id = ANY($1::bigint[])", obs_ids)
        by_type: dict[str, list[Decimal]] = {}
        psqm: dict[str, list[Decimal]] = {}
        latest: dict[str, tuple[datetime, Decimal]] = {}
        for p in prices:
            by_type.setdefault(p["price_type"], []).append(Decimal(p["amount_lak"]))
            if p["price_per_sqm_lak"] is not None:
                psqm.setdefault(p["price_type"], []).append(Decimal(p["price_per_sqm_lak"]))
            if p["price_type"] not in latest or p["observed_at"] > latest[p["price_type"]][0]:
                latest[p["price_type"]] = (p["observed_at"], Decimal(p["amount_lak"]))
        asking = {}
        for t, vals in by_type.items():
            med = Decimal(median(vals))
            f = lambda v: format(Decimal(v).quantize(Decimal(1)), "f")  # noqa: E731 — plain integers, never scientific notation
            asking[t] = {"min_lak": f(min(vals)), "max_lak": f(max(vals)), "median_lak": f(med), "latest_lak": f(latest[t][1]),
                         "dispersion": round(float((max(vals) - min(vals)) / med), 4) if med else None, "n": len(vals),
                         "per_sqm_median_lak": f(median(psqm[t])) if t in psqm else None}
        scores = [r["score"] for r in rows if r["score"] is not None]
        human = any(r["source"] == "HUMAN" and r["decision"] == "CONFIRMED" for r in rows)
        conf = 100.0 if human else (sum(scores) / len(scores) if scores else 0.0)
        review_state = "PENDING_REVIEW" if any(r["decision"] == "REVIEW_REQUIRED" for r in rows) else "CLEAN"
        await con.execute(
            """INSERT INTO market.property_stats_snapshots (market_property_id, stats_version, observation_count, advertiser_count, cluster_count, first_observed, last_observed, asking_stats, resolution_confidence, review_state)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10)""",
            mp, stats_version, len(rows), adv or 0, clusters or 0, min(r["observed_at"] for r in rows), max(r["observed_at"] for r in rows), _j(asking), round(conf, 2), review_state)

    # ---------------------------------------------------------------- read

    async def list_properties(self, district: str | None, village: str | None, asset: str | None, state: str | None, limit: int) -> list[dict[str, Any]]:
        async with self.db.pool.acquire() as con:
            rows = await con.fetch(
                """SELECT p.market_property_id, p.status, p.asset_type, p.created_at, s.observation_count, s.advertiser_count, s.asking_stats, s.resolution_confidence, s.review_state, s.computed_at,
                          l.district_code, l.village_code, l.precision
                   FROM market.properties p
                   LEFT JOIN LATERAL (SELECT * FROM market.property_stats_snapshots x WHERE x.market_property_id=p.market_property_id ORDER BY computed_at DESC LIMIT 1) s ON true
                   LEFT JOIN LATERAL (SELECT district_code, village_code, precision FROM geo.resolved_locations g WHERE g.observation_id=p.created_from_observation_id AND g.is_primary ORDER BY resolved_location_id DESC LIMIT 1) l ON true
                   WHERE p.status='ACTIVE' AND ($1::text IS NULL OR l.district_code=$1) AND ($2::text IS NULL OR l.village_code=$2)
                     AND ($3::text IS NULL OR p.asset_type=$3) AND ($4::text IS NULL OR s.review_state=$4)
                   ORDER BY s.computed_at DESC NULLS LAST, p.created_at DESC LIMIT $5""", district, village, asset, state, limit)
        return [_row(r) for r in rows]

    async def get_property(self, mp: str) -> dict[str, Any] | None:
        async with self.db.pool.acquire() as con:
            p = await con.fetchrow("SELECT * FROM market.properties WHERE market_property_id=$1", mp)
            if not p:
                return None
            snap = await con.fetchrow("SELECT * FROM market.property_stats_snapshots WHERE market_property_id=$1 ORDER BY computed_at DESC LIMIT 1", mp)
            obs = await con.fetch(
                """SELECT d.decision_id, d.observation_id, d.decision, d.source, d.reviewer, d.created_at, o.record_key, o.signal_class, o.asset_type, o.post_date,
                          cd.score, cd.signals
                   FROM resolution.current_decisions d JOIN extract.observations o ON o.observation_id=d.observation_id
                   LEFT JOIN resolution.entity_candidates cd ON cd.candidate_id=d.candidate_id
                   WHERE d.market_property_id=$1 ORDER BY d.observation_id""", mp)
            hist = await con.fetch("SELECT decision_id, observation_id, decision, source, supersedes_decision_id, created_at FROM resolution.entity_decisions WHERE market_property_id=$1 ORDER BY decision_id", mp)
            prices = await con.fetch("SELECT p.* FROM extract.price_observations p JOIN resolution.current_decisions d ON d.observation_id=p.observation_id WHERE d.market_property_id=$1 ORDER BY p.observed_at", mp)
        return {**_row(p), "stats": _row(snap) if snap else None, "observations": [_row(r) for r in obs], "price_observations": [_row(r) for r in prices], "decision_history": [_row(r) for r in hist]}

    async def stats(self) -> dict[str, Any]:
        async with self.db.pool.acquire() as con:
            by_dec = await con.fetch("SELECT decision, count(*) AS n FROM resolution.current_decisions GROUP BY 1")
            props = await con.fetchrow("SELECT count(*) FILTER (WHERE status='ACTIVE') AS active, count(*) AS total FROM market.properties")
            multi = await con.fetchval("SELECT count(*) FROM (SELECT market_property_id FROM resolution.current_decisions GROUP BY 1 HAVING count(*) > 1) x")
            review_age = await con.fetchrow("SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM now()-created_at)/86400) AS p95_days FROM resolution.current_decisions WHERE decision='REVIEW_REQUIRED'")
            runs = await con.fetch("SELECT run_id, started_at, finished_at, trigger, match_version, records_in, clusters_out, candidates_out, decisions_out, error FROM resolution.runs ORDER BY run_id DESC LIMIT 10")
            clusters = await con.fetchval("SELECT count(*) FROM resolution.listing_clusters WHERE status='ACTIVE'")
            explain = await con.fetchval(
                """SELECT count(*) FROM resolution.current_decisions d JOIN resolution.entity_candidates c ON c.candidate_id=d.candidate_id
                   WHERE d.source <> 'HUMAN' AND c.score <> LEAST(100, GREATEST(0, (SELECT COALESCE(sum((s->>'contribution')::int),0) FROM jsonb_array_elements(c.signals) s)))""")
        return {"properties_active": props["active"], "properties_total": props["total"], "multi_observation_properties": multi, "clusters_active": clusters,
                "by_decision": {r["decision"]: r["n"] for r in by_dec}, "review_queue_p95_age_days": round(float(review_age["p95_days"]), 2) if review_age and review_age["p95_days"] is not None else None,
                "unexplained_machine_decisions": explain, "runs": [_row(r) for r in runs]}


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, Decimal):
            d[k] = format(v.normalize(), "f") if v == v.to_integral() else format(v, "f")
        elif isinstance(v, float):
            d[k] = round(v, 4)
        elif isinstance(v, str) and k in ("signals", "asking_stats", "blocking_reasons"):
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    return d


ENABLE_FLAG = "LENS_RESOLUTION_ENABLED"


def resolution_enabled() -> bool:
    return os.environ.get(ENABLE_FLAG, "0") in ("1", "true", "yes")
