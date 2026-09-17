"""Resolution run (001E §10): clusters → blocking → MATCH_V1 → candidates + decisions + market properties + stats.

Rules honoured:
- Observations with a current HUMAN decision are never re-decided (E4); they still serve as targets.
- Machine re-runs on an already-decided observation happen only with `force` (new candidate + superseding decision).
- A decision links an observation to a market property: the target's property on HIGH; a provisional singleton
  property on REVIEW_REQUIRED (with candidates recorded for review); a singleton on SEPARATE.
- Deterministic: observations processed in id order; targets chosen by (score desc, target property id asc).
"""
from __future__ import annotations

import logging
from typing import Any

from .blocking import BLOCKING_VERSION, block
from .cluster import CLUSTER_VERSION, build_clusters
from .match import MATCH_VERSION, score_pair
from .permalink import PERMALINK_RULES_VERSION

log = logging.getLogger("lens-api.resolution")
VERSIONS = {"permalink": PERMALINK_RULES_VERSION, "cluster": CLUSTER_VERSION, "blocking": BLOCKING_VERSION, "match": MATCH_VERSION}
MACHINE_SOURCE = f"MATCH_V{MATCH_VERSION}"


async def run_resolution(store: Any, *, trigger: str, force: bool = False) -> dict[str, Any]:
    run_id = await store.start_run(trigger, VERSIONS)
    counts = {"records_in": 0, "clusters": 0, "candidates": 0, "decisions": 0, "properties": 0}
    error: str | None = None
    try:
        rows = await store.load()
        counts["records_in"] = len(rows)
        # ---- 1. listing clusters (append-only; ids deterministic)
        cl_inputs = [store.to_cluster_input(r) for r in rows]
        clusters, _ = build_clusters(cl_inputs)
        counts["clusters"] = await store.write_clusters(run_id, clusters)
        member_cluster = {m: c.cluster_id for c in clusters for m in c.members}
        for r in rows:
            r["cluster_id"] = r.get("cluster_id") or member_cluster.get(r["record_key"])
        # ---- 2. blocking + scoring
        sides = [store.to_side(r) for r in rows]
        blk = block(sides)
        pair_scores: dict[tuple[int, int], Any] = {}
        for i, j in blk.pairs:
            pair_scores[(i, j)] = score_pair(sides[i], sides[j])
        # ---- 3. decisions, greedy in observation order
        mp_of: dict[int, str] = {i: r["current_mp"] for i, r in enumerate(rows) if r["current_mp"] and r["current_decision"] not in ("REJECTED", "UNLINKED")}
        async with store.db.pool.acquire() as con:
            async with con.transaction():
                touched: set[str] = set()
                for i, r in enumerate(rows):
                    if r["current_source"] == "HUMAN":
                        continue  # E4: human decisions stand
                    if r["current_mp"] and not force:
                        continue
                    if i in blk.ineligible:
                        continue  # asset UNKNOWN etc. (E3); unlocatable ones simply have no pairs and become singletons
                    # best target among peers that already have a property: score desc, then property id asc (deterministic)
                    best: tuple[int, str, int, Any, list[str]] | None = None
                    for (a, b), sc in pair_scores.items():
                        if i not in (a, b):
                            continue
                        peer = b if a == i else a
                        peer_mp = mp_of.get(peer)
                        if not peer_mp:
                            continue
                        cand = (sc.total, peer_mp, peer, sc, blk.reasons.get((min(i, peer), max(i, peer)), []))
                        if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] < best[1]):
                            best = cand
                    supersedes = await store.current_decision_id(con, r["observation_id"])
                    if best and best[3].decision == "HIGH_CONFIDENCE_MATCH":
                        cid = await store.write_candidate(con, run_id, r["observation_id"], sides[best[2]].observation_id, best[1], best[3], best[4], VERSIONS)
                        await store.write_decision(con, run_id, r["observation_id"], best[1], "HIGH_CONFIDENCE_MATCH", MACHINE_SOURCE, cid, supersedes)
                        mp_of[i] = best[1]
                        counts["candidates"] += 1 if cid else 0
                        touched.add(best[1])
                    else:
                        mp = mp_of.get(i) or await store.new_property(con, r["observation_id"], run_id, sides[i].asset_type)
                        if i not in mp_of:
                            counts["properties"] += 1
                        decision = "REVIEW_REQUIRED" if best and best[3].decision == "REVIEW_REQUIRED" else "SEPARATE_CANDIDATE"
                        cid = None
                        # record every scored candidate for explainability / the review queue
                        for (a, b), sc in pair_scores.items():
                            if i in (a, b):
                                peer = b if a == i else a
                                c = await store.write_candidate(con, run_id, r["observation_id"], sides[peer].observation_id, mp_of.get(peer), sc, blk.reasons.get((min(i, peer), max(i, peer)), []), VERSIONS)
                                counts["candidates"] += 1 if c else 0
                                if best and peer == best[2]:
                                    cid = c
                        await store.write_decision(con, run_id, r["observation_id"], mp, decision, MACHINE_SOURCE, cid, supersedes)
                        mp_of[i] = mp
                        touched.add(mp)
                    counts["decisions"] += 1
                # ---- 4. statistics snapshots for touched properties
                for mp in sorted(touched):
                    await store.snapshot_stats(con, mp)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        await store.finish_run(run_id, counts, error)
    return {"run_id": run_id, **counts}
