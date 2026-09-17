"""HK-001 §5 lineage walk over existing keys (no lineage table in v0.1, decision Q2).

Identifier shapes: 64-hex → payload hash (L0); `<platform>:<post_id>` → record (L1); `obs:<n>` or a bare integer → observation;
`loc:<n>` → resolved location; `dec:<n>` → entity decision; `MP-…` → market property; `snap:<n>` → snapshot.
Returns nodes (type, id, produced_by {job, version, run}) and edges, upstream and downstream, counts only — never text or contacts.
"""
from __future__ import annotations

import re
from typing import Any

_HEX64 = re.compile(r"^[0-9a-f]{64}$", re.I)
_MP = re.compile(r"^MP-[0-9A-HJKMNP-TV-Z]{26}$")


def classify_id(ident: str) -> tuple[str, str] | None:
    s = ident.strip()
    if _HEX64.match(s):
        return "payload", s.lower()
    if _MP.match(s):
        return "market_property", s
    for prefix, t in (("obs:", "observation"), ("loc:", "location"), ("dec:", "decision"), ("snap:", "snapshot")):
        if s.startswith(prefix) and s[len(prefix):].isdigit():
            return t, s[len(prefix):]
    if s.isdigit():
        return "observation", s
    if re.match(r"^(facebook|tiktok):[A-Za-z0-9_.-]+$", s):
        return "record", s
    return None


async def lineage(db: Any, ident: str) -> dict[str, Any] | None:
    c = classify_id(ident)
    if not c:
        return None
    kind, key = c
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def node(t: str, i: Any, **attrs: Any) -> None:
        if any(n["type"] == t and n["id"] == str(i) for n in nodes):
            return
        produced = {k: attrs.pop(k) for k in ("job", "version", "run") if k in attrs}
        nodes.append({"type": t, "id": str(i), **({"produced_by": produced} if produced else {}), **attrs})

    def edge(a: tuple[str, Any], b: tuple[str, Any], via: str) -> None:
        edges.append({"from": {"type": a[0], "id": str(a[1])}, "to": {"type": b[0], "id": str(b[1])}, "via": via})

    async with db.pool.acquire() as con:
        res_on = await con.fetchval("SELECT to_regclass('resolution.current_decisions') IS NOT NULL")
        # resolve the seed to a set of record keys / observation ids to expand from
        record_keys: list[str] = []
        obs_ids: list[int] = []
        if kind == "payload":
            row = await con.fetchrow("SELECT payload_hash, platform, captured_at, body IS NOT NULL AS body_present, body_purged_at FROM raw_captures WHERE payload_hash=$1", key)
            if not row:
                return {"seed": {"type": kind, "id": key}, "nodes": [], "edges": [], "note": "no raw capture with this hash (evidence never received or purged rows keep the hash — check capture_events)"}
            node("payload", key, job="extension.capture", body_present=row["body_present"])
            record_keys = [r["record_key"] for r in await con.fetch("SELECT DISTINCT record_key FROM capture_events WHERE payload_hash=$1", key)]
            for rk in record_keys:
                edge(("payload", key), ("record", rk), "capture_events")
        elif kind == "record":
            record_keys = [key]
        elif kind == "observation":
            rk = await con.fetchval("SELECT record_key FROM extract.observations WHERE observation_id=$1", int(key))
            if rk is None:
                return None
            record_keys, obs_ids = [rk], [int(key)]
        elif kind == "location":
            oid = await con.fetchval("SELECT observation_id FROM geo.resolved_locations WHERE resolved_location_id=$1", int(key))
            if oid is None:
                return None
            obs_ids = [oid]
            record_keys = [await con.fetchval("SELECT record_key FROM extract.observations WHERE observation_id=$1", oid)]
        elif kind in ("decision", "market_property", "snapshot"):
            if not res_on:
                return {"seed": {"type": kind, "id": key}, "nodes": [], "edges": [], "note": "resolution disabled (LENS_RESOLUTION_ENABLED=0)"}
            if kind == "decision":
                oid = await con.fetchval("SELECT observation_id FROM resolution.entity_decisions WHERE decision_id=$1", int(key))
                if oid is None:
                    return None
                obs_ids = [oid]
            elif kind == "snapshot":
                mp = await con.fetchval("SELECT market_property_id FROM market.property_stats_snapshots WHERE snapshot_id=$1", int(key))
                if mp is None:
                    return None
                key, kind = mp, "market_property"
            if kind == "market_property":
                obs_ids = [r["observation_id"] for r in await con.fetch("SELECT observation_id FROM resolution.current_decisions WHERE market_property_id=$1 AND decision NOT IN ('REJECTED','UNLINKED')", key)]
                if not obs_ids and not await con.fetchval("SELECT 1 FROM market.properties WHERE market_property_id=$1", key):
                    return None
            record_keys = [r["record_key"] for r in await con.fetch("SELECT DISTINCT record_key FROM extract.observations WHERE observation_id = ANY($1::bigint[])", obs_ids)]

        # ---- L0 ← L1
        for rk in record_keys:
            rec = await con.fetchrow("SELECT key, parser_version, first_payload_hash, last_payload_hash, capture_count, content_hash, protected FROM records WHERE key=$1", rk)
            if not rec:
                continue
            node("record", rk, job="extension.parser", version=rec["parser_version"], capture_count=rec["capture_count"], protected=rec["protected"])
            for ph in {rec["first_payload_hash"], rec["last_payload_hash"]} - {None}:
                raw = await con.fetchrow("SELECT body IS NOT NULL AS body_present FROM raw_captures WHERE payload_hash=$1", ph)
                node("payload", ph, job="extension.capture", body_present=bool(raw and raw["body_present"]), received=raw is not None)
                edge(("payload", ph), ("record", rk), "records.first/last_payload_hash")
            events = await con.fetch("SELECT captured_at, context, payload_hash FROM capture_events WHERE record_key=$1 ORDER BY captured_at", rk)
            rec_node = next(n for n in nodes if n["type"] == "record" and n["id"] == rk)
            rec_node["sightings"] = [{"at": e["captured_at"], "context": e["context"], "payload_present": e["payload_hash"] is not None} for e in events][:20]
            rec_node["sighting_count"] = len(events)
            # ---- L1 → L2 extraction (current + retired)
            obs = await con.fetch("""SELECT o.observation_id, o.run_id, o.method_version, o.signal_class, o.asset_type, o.observed_at, o.content_hash,
                                            (o.observation_id IN (SELECT observation_id FROM extract.current_observations)) AS is_current
                                     FROM extract.observations o WHERE o.record_key=$1 ORDER BY o.observation_id""", rk)
            for o in obs:
                if obs_ids and o["observation_id"] not in obs_ids and o["is_current"] is False:
                    continue
                node("observation", o["observation_id"], job="extract.rule_v1", version=o["method_version"], run=o["run_id"], signal_class=o["signal_class"], asset_type=o["asset_type"], current=o["is_current"])
                edge(("record", rk), ("observation", o["observation_id"]), f"extract.runs#{o['run_id']}")
                counts = await con.fetchrow("SELECT count(*) FILTER (WHERE field<>'CONTACT') claims, count(*) FILTER (WHERE field='CONTACT') contact_claims FROM extract.claims WHERE observation_id=$1", o["observation_id"])
                obs_node = next(n for n in nodes if n["type"] == "observation" and n["id"] == str(o["observation_id"]))
                obs_node["claims"] = counts["claims"]
                obs_node["contact_claims"] = counts["contact_claims"]
                for loc in await con.fetch("SELECT resolved_location_id, precision, resolver_method, resolver_version, is_primary, review_status, supersedes_id FROM geo.resolved_locations WHERE observation_id=$1 ORDER BY resolved_location_id", o["observation_id"]):
                    node("location", loc["resolved_location_id"], job=loc["resolver_method"], version=loc["resolver_version"], precision=loc["precision"], primary=loc["is_primary"], review_status=loc["review_status"])
                    edge(("observation", o["observation_id"]), ("location", loc["resolved_location_id"]), "geo.resolved_locations")
                    if loc["supersedes_id"]:
                        edge(("location", loc["supersedes_id"]), ("location", loc["resolved_location_id"]), "supersedes")
                if res_on:
                    for d in await con.fetch("SELECT decision_id, market_property_id, decision, source, reviewer IS NOT NULL AS human, run_id, supersedes_decision_id, (decision_id IN (SELECT decision_id FROM resolution.current_decisions)) AS is_current FROM resolution.entity_decisions WHERE observation_id=$1 ORDER BY decision_id", o["observation_id"]):
                        node("decision", d["decision_id"], job=d["source"], run=d["run_id"], decision=d["decision"], human=d["human"], current=d["is_current"])
                        edge(("observation", o["observation_id"]), ("decision", d["decision_id"]), f"resolution.runs#{d['run_id']}" if d["run_id"] else "review")
                        if d["supersedes_decision_id"]:
                            edge(("decision", d["supersedes_decision_id"]), ("decision", d["decision_id"]), "supersedes")
                        mp = await con.fetchrow("SELECT status, superseded_by FROM market.properties WHERE market_property_id=$1", d["market_property_id"])
                        node("market_property", d["market_property_id"], status=mp["status"] if mp else None)
                        edge(("decision", d["decision_id"]), ("market_property", d["market_property_id"]), "entity_decisions.market_property_id")
                        if mp and mp["superseded_by"]:
                            node("market_property", mp["superseded_by"])
                            edge(("market_property", d["market_property_id"]), ("market_property", mp["superseded_by"]), "superseded_by")
        if res_on:
            for n in [n for n in nodes if n["type"] == "market_property"]:
                for s in await con.fetch("SELECT snapshot_id, stats_version, computed_at, dq_grade, observation_count FROM market.property_stats_snapshots WHERE market_property_id=$1 ORDER BY computed_at DESC LIMIT 3", n["id"]):
                    node("snapshot", s["snapshot_id"], job="snapshot", version=s["stats_version"], computed_at=s["computed_at"], dq_grade=s["dq_grade"], observation_count=s["observation_count"])
                    edge(("market_property", n["id"]), ("snapshot", s["snapshot_id"]), "property_stats_snapshots")
                if await con.fetchval("SELECT to_regclass('publish.version_members') IS NOT NULL"):
                    for m in await con.fetch("SELECT dataset_id, version FROM publish.version_members WHERE market_property_id=$1", n["id"]):
                        dv = f"{m['dataset_id']}@{m['version']}"
                        node("dataset_version", dv)
                        edge(("market_property", n["id"]), ("dataset_version", dv), "publish.version_members")
    return {"seed": {"type": kind, "id": key}, "nodes": nodes, "edges": edges}
