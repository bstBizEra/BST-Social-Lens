"""001E live: clusters → blocking → MATCH_V1 → decisions/properties/stats on the 001F draft schema (LENS_TEST_DSN, flag on)."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

DSN = os.environ.get("LENS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="LENS_TEST_DSN not set")

os.environ.setdefault("LENS_API_TOKEN", "test-token")
os.environ["LENS_EXTRACT_INTERVAL_MIN"] = "0"
os.environ["LENS_RESOLUTION_ENABLED"] = "1"
os.environ["LENS_RESOLVE_INTERVAL_MIN"] = "0"

# Same 20x30 land in Dongdok pinned at (18.052,102.661) — the test polygon — advertised by three agents + the owner,
# one exact re-post, and a decoy in Pakse (different district → forced SEPARATE).
PIN = "https://maps.google.com/?q=18.052,102.661"
POSTS = [
    ("facebook:m1", "ຂາຍດິນ 20x30 ບ້ານດົງໂດກ ລາຄາ 2.5 ຕື້ ໂທ 020 5511 1111 " + PIN, "a1"),
    ("facebook:m2", "ຂາຍດິນງາມ 20x30 ດົງໂດກ ຕິດຕໍ່ນາຍໜ້າ ລາຄາ 2.7 ຕື້ ໂທ 020 5522 2222 " + PIN, "a2"),
    ("facebook:m3", "ຂາຍດິນ 600 ຕາແມັດ ບ້ານດົງໂດກ ລາຄາ 2.45 ຕື້ ໂທ 020 5533 3333 " + PIN, "a3"),
    ("facebook:m4", "ເຈົ້າຂອງຂາຍເອງ ດິນ 20x30 ບ້ານດົງໂດກ ລາຄາ 2.3 ຕື້ ໂທ 020 5544 4444 " + PIN, "own"),
    ("facebook:m5", "ຂາຍດິນ 20x30 ບ້ານດົງໂດກ ລາຄາ 2.5 ຕື້ ໂທ 020 5511 1111 " + PIN, "a1"),      # exact re-post of m1 → same cluster
    ("facebook:m6", "ຂາຍດິນ 20x30 ເມືອງປາກເຊ ລາຄາ 2.5 ຕື້ ໂທ 020 5566 6666", "a6"),               # decoy, different district
    ("facebook:m7", "ຂາຍດິນ ບ້ານດົງໂດກ ລາຄາ 3.2 ຕື້ ໂທ 020 5577 7777 " + PIN, "a7"),               # same pin, no size, other price → review band
]


@pytest.fixture(scope="module")
def env():
    from app import main as main_mod
    from app.extract.service import run_extraction

    async def setup():
        main = main_mod
        main.db._dsn = DSN
        await main.db.connect()
        async with main.db.pool.acquire() as con:
            await con.execute("""DELETE FROM housekeeping.actions; DELETE FROM housekeeping.findings; DELETE FROM housekeeping.checks; DELETE FROM housekeeping.watermarks; DELETE FROM housekeeping.lifecycle_states; DELETE FROM housekeeping.runs;
                                 DELETE FROM market.property_stats_snapshots; DELETE FROM resolution.entity_decisions; DELETE FROM resolution.entity_candidates;
                                 DELETE FROM resolution.property_transitions; DELETE FROM market.properties; DELETE FROM resolution.cluster_edges;
                                 DELETE FROM resolution.cluster_members; DELETE FROM resolution.listing_clusters; DELETE FROM resolution.runs;
                                 DELETE FROM audit.events; DELETE FROM geo.resolved_locations; DELETE FROM extract.contact_sightings; DELETE FROM extract.price_observations;
                                 DELETE FROM extract.claims; DELETE FROM extract.observations; DELETE FROM extract.runs; DELETE FROM extract.contact_points;
                                 DELETE FROM geo.name_aliases; DELETE FROM geo.villages; DELETE FROM geo.districts; DELETE FROM geo.provinces; DELETE FROM geo.admin_versions;""")
            # isolate from other live suites sharing the throwaway DB (test_extract_live uses facebook:x*)
            await con.execute("DELETE FROM capture_events WHERE record_key LIKE 'facebook:m%' OR record_key LIKE 'facebook:x%'")
            await con.execute("DELETE FROM records WHERE key LIKE 'facebook:m%' OR key LIKE 'facebook:x%'")
            for key, text, author in POSTS:
                await con.execute(
                    "INSERT INTO records (key, platform, post_id, record_type, text, author_hash, captured_at, created_at, content_hash) VALUES ($1,'facebook',$2,'post',$3,$4,$5,$6,$7)",
                    key, key.split(":")[1], text, "h-" + author, datetime.now(timezone.utc), datetime(2026, 9, 12, tzinfo=timezone.utc), "c-" + key)
        fixture = json.loads((Path(__file__).parent / "fixtures" / "geo" / "admin-sample.json").read_text(encoding="utf-8"))
        main.geo_store._gaz = None
        await main.geo_store.import_admin(fixture, "fixture")
        await run_extraction(main.extract_store, trigger="test", pgcrypto=main.db.pgcrypto, contact_salt="s")
        return main

    loop = asyncio.new_event_loop()
    main_mod.loop = loop
    loop.run_until_complete(setup())
    yield main_mod
    loop.run_until_complete(main_mod.db.close())
    loop.close()


def test_resolution_links_multi_agent_land_and_keeps_every_observation(env):
    from app.resolution.service import run_resolution

    main = env
    res = main.loop.run_until_complete(run_resolution(main.resolution_store, trigger="test"))
    assert res["records_in"] == 7 and res["decisions"] == 7 and res["clusters"] == 1

    async def read():
        async with main.db.pool.acquire() as con:
            dec = await con.fetch("SELECT d.observation_id, o.record_key, d.market_property_id, d.decision, d.source FROM resolution.current_decisions d JOIN extract.observations o USING (observation_id) ORDER BY 1")
            props = await con.fetch("SELECT market_property_id, status FROM market.properties ORDER BY 1")
            obs_n = await con.fetchval("SELECT count(*) FROM extract.observations")
            price_n = await con.fetchval("SELECT count(*) FROM extract.price_observations")
            clus = await con.fetch("SELECT cluster_id, array_agg(record_key ORDER BY record_key) AS m FROM resolution.cluster_members GROUP BY 1")
            snaps = await con.fetch("SELECT market_property_id, observation_count, advertiser_count, cluster_count, asking_stats::text, review_state, resolution_confidence, dq_grade FROM market.property_stats_snapshots ORDER BY computed_at")
            return [dict(r) for r in dec], [dict(r) for r in props], obs_n, price_n, [dict(r) for r in clus], [dict(r) for r in snaps]

    dec, props, obs_n, price_n, clus, snaps = main.loop.run_until_complete(read())
    by_key = {d["record_key"]: d for d in dec}
    # the exact re-post clusters with its original
    assert clus == [{"cluster_id": "C-facebook:m1", "m": ["facebook:m1", "facebook:m5"]}]
    # all four advertisers + the re-post land on ONE property; the Pakse decoy stays separate (location_conflict)
    land_mp = by_key["facebook:m1"]["market_property_id"]
    same = {k for k, d in by_key.items() if d["market_property_id"] == land_mp}
    assert same == {"facebook:m1", "facebook:m2", "facebook:m3", "facebook:m4", "facebook:m5"}, same
    assert by_key["facebook:m6"]["market_property_id"] != land_mp
    # Dongdok: one seed opens a property (SEPARATE_CANDIDATE), four link HIGH, and m7 (pin only, no size) lands in the review
    # band against whichever of m5/m7 was processed first — exactly one REVIEW_REQUIRED item, on its own provisional property.
    dd = ["facebook:m1", "facebook:m2", "facebook:m3", "facebook:m4", "facebook:m5", "facebook:m7"]
    decisions = sorted(by_key[k]["decision"] for k in dd)
    assert decisions == ["HIGH_CONFIDENCE_MATCH"] * 4 + ["REVIEW_REQUIRED", "SEPARATE_CANDIDATE"], decisions
    review_key = next(k for k in dd if by_key[k]["decision"] == "REVIEW_REQUIRED")
    assert review_key in ("facebook:m5", "facebook:m7") and by_key["facebook:m7"]["market_property_id"] != land_mp
    assert "HIGH_CONFIDENCE_MATCH" in (by_key["facebook:m1"]["decision"], by_key["facebook:m5"]["decision"])  # same cluster forces HIGH
    assert all(d["source"].startswith("MATCH_V") for d in dec)
    # nothing collapsed: 7 observations, 7 price observations, 3 properties, prices 2.3–2.7 all present in the land snapshot
    assert obs_n == 7 and price_n == 7 and len(props) == 3 and all(p["status"] == "ACTIVE" for p in props)
    snap = next(s for s in reversed(snaps) if s["market_property_id"] == land_mp)
    stats = json.loads(snap["asking_stats"])["ASKING_SALE"]
    assert snap["observation_count"] == 5 and snap["advertiser_count"] == 4 and snap["cluster_count"] == 1 and snap["dq_grade"] in "ABCD"
    assert stats["min_lak"] == "2300000000" and stats["max_lak"] == "2700000000" and stats["n"] == 5 and stats["dispersion"] > 0.1


def test_rerun_is_idempotent_and_human_decision_is_respected(env):
    from app.resolution.service import run_resolution

    main = env
    before = main.loop.run_until_complete(main.resolution_store.stats())
    res = main.loop.run_until_complete(run_resolution(main.resolution_store, trigger="test"))
    assert res["decisions"] == 0 and res["clusters"] == 0  # everything already decided; no new cluster rows

    async def human_unlink():
        async with main.db.pool.acquire() as con:
            d = await con.fetchrow("SELECT d.decision_id, d.observation_id, d.market_property_id FROM resolution.current_decisions d JOIN extract.observations o USING (observation_id) WHERE o.record_key='facebook:m2'")
            await con.execute("INSERT INTO resolution.entity_decisions (observation_id, market_property_id, decision, source, reviewer, supersedes_decision_id) VALUES ($1,$2,'UNLINKED','HUMAN','vily',$3)", d["observation_id"], d["market_property_id"], d["decision_id"])
            return d["observation_id"]

    obs_id = main.loop.run_until_complete(human_unlink())
    res = main.loop.run_until_complete(run_resolution(main.resolution_store, trigger="test", force=True))
    after = main.loop.run_until_complete(main.resolution_store.stats())

    async def check():
        async with main.db.pool.acquire() as con:
            cur = await con.fetchrow("SELECT decision, source FROM resolution.current_decisions WHERE observation_id=$1", obs_id)
            hist = await con.fetchval("SELECT count(*) FROM resolution.entity_decisions WHERE observation_id=$1", obs_id)
            return dict(cur), hist

    cur, hist = main.loop.run_until_complete(check())
    assert cur == {"decision": "UNLINKED", "source": "HUMAN"} and hist == 2  # force re-run did not touch the human row (E4)
    assert res["decisions"] == 6  # the other six were re-decided (new rows), history grows
    assert after["unexplained_machine_decisions"] == 0 and before["unexplained_machine_decisions"] == 0
    assert after["properties_total"] >= before["properties_total"]  # ids never deleted


def test_http_and_mcp_market_reads(env):
    from fastapi.testclient import TestClient

    main = env
    h = {"Authorization": "Bearer test-token"}
    with TestClient(main.app) as client:
        r = client.get("/market/properties?village=V-XTN-DDK", headers=h)
        assert r.status_code == 200 and r.json()["count"] >= 2  # the land property + m7's provisional singleton
        mp = max(r.json()["properties"], key=lambda p: (p.get("observation_count") or 0))["market_property_id"]
        p = client.get(f"/market/properties/{mp}", headers=h).json()
        assert p["stats"]["asking_stats"]["ASKING_SALE"]["n"] >= 4 and len(p["decision_history"]) >= len(p["observations"])
        assert all("signals" in o for o in p["observations"])
        assert "raw_value" not in json.dumps(p) and "author_hash" not in json.dumps(p)
        s = client.get("/resolution/stats", headers=h).json()
        assert s["unexplained_machine_decisions"] == 0 and s["clusters_active"] == 1
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_market_property", "arguments": {"market_property_id": mp}}})
        assert mcp.status_code == 200 and "asking_stats" in mcp.text
        r = client.post("/admin/resolve", headers=h)
        assert r.status_code == 200 and r.json()["decisions"] == 0


def test_match_queue_csv_round_trip_and_recompute(env, tmp_path):
    """001G §6 match queue: export the REVIEW_REQUIRED item, CONFIRM it → HUMAN CONFIRMED on the proposed property,
    provisional singleton SUPERSEDED with a MERGE transition, one audit row, snapshots recomputed; then /admin/quality/recompute."""
    import csv
    import subprocess
    import sys as _sys

    main = env
    envv = {**os.environ, "LENS_DB_DSN": DSN, "LENS_REVIEWER": "vily"}
    run = lambda *a: subprocess.run([_sys.executable, "scripts/review.py", *a], cwd=Path(__file__).resolve().parents[1], env=envv, capture_output=True, text=True)  # noqa: E731
    r = run("export", "--queue", "match", "--out", str(tmp_path))
    assert r.returncode == 0, r.stderr
    path = tmp_path / "review-match.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["record_key"] in ("facebook:m5", "facebook:m7") and rows[0]["proposed_property_id"] and "raw_value" not in path.read_text(encoding="utf-8")
    assert "5577" not in path.read_text(encoding="utf-8") and "5511" not in path.read_text(encoding="utf-8")  # contact masked in context
    own, proposed = rows[0]["market_property_id"], rows[0]["proposed_property_id"]
    rows[0]["action"], rows[0]["reason"] = "CONFIRM", "same pin, owner confirmed by phone"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    r = run("import", "--file", str(path), "--reviewer", "vily")
    assert r.returncode == 0 and "imported 1 action" in r.stdout, r.stdout + r.stderr
    r = run("import", "--file", str(path), "--reviewer", "vily")  # second import: item already reviewed → skipped, nothing written
    assert "already reviewed" in r.stdout and "imported 0" in r.stdout

    async def check():  # fresh connection: the earlier TestClient lifespan closed the module pool
        import asyncpg

        con = await asyncpg.connect(DSN)
        try:
            cur = await con.fetch("SELECT o.record_key, d.decision, d.source, d.market_property_id FROM resolution.current_decisions d JOIN extract.observations o USING (observation_id) WHERE o.record_key LIKE 'facebook:m%' ORDER BY 1")
            props = await con.fetch("SELECT market_property_id, status, superseded_by FROM market.properties WHERE market_property_id IN ($1,$2)", own, proposed)
            tr = await con.fetch("SELECT source_property_id, target_property_ids FROM resolution.property_transitions WHERE kind='MERGE'")
            audit = await con.fetch("SELECT action, after::text AS after FROM audit.events WHERE queue='match'")
            survivor = next(p["market_property_id"] for p in props if p["status"] == "ACTIVE")
            snap = await con.fetchrow("SELECT observation_count, dq_grade FROM market.property_stats_snapshots WHERE market_property_id=$1 ORDER BY computed_at DESC LIMIT 1", survivor)
            return {r["record_key"]: dict(r) for r in cur}, {p["market_property_id"]: dict(p) for p in props}, [dict(r) for r in tr], audit, dict(snap), survivor
        finally:
            await con.close()

    cur, props, tr, audit, snap, survivor = asyncio.run(check())
    absorbed = proposed if survivor == own else own
    # every Dongdok observation except the human-UNLINKED m2 now sits on the surviving property; the absorbed one is SUPERSEDED, not deleted
    assert {cur[k]["market_property_id"] for k in ("facebook:m1", "facebook:m3", "facebook:m4", "facebook:m5", "facebook:m7")} == {survivor}
    assert cur["facebook:m2"]["decision"] == "UNLINKED" and cur["facebook:m6"]["market_property_id"] not in (own, proposed)
    assert props[absorbed]["status"] == "SUPERSEDED" and props[absorbed]["superseded_by"] == survivor and props[survivor]["status"] == "ACTIVE"
    assert tr == [{"source_property_id": absorbed, "target_property_ids": [survivor]}]
    assert len(audit) == 1 and audit[0]["action"] == "CONFIRM" and '"merged_observations": 1' in audit[0]["after"]
    assert snap["observation_count"] == 5 and snap["dq_grade"] in "ABCD"

    from fastapi.testclient import TestClient

    h = {"Authorization": "Bearer test-token"}
    with TestClient(main.app) as client:
        r = client.post("/admin/quality/recompute", headers=h)
        assert r.status_code == 200 and r.json()["properties"] >= 2 and sum(r.json()["latest_dq_grades"].values()) >= 2, r.json()
        q = client.get("/quality/stats", headers=h).json()
        assert q["entity_match_source"] == "resolution.current_decisions"
        r = client.post("/admin/quality/recompute?since=2999-01-01T00:00:00Z", headers=h)
        assert r.status_code == 200 and r.json()["properties"] == 0


def test_housekeeping_status_with_resolution(env):
    """HK-001 read-only status on a populated DB with the flag on: every stage reports, reconciliation ratios are sane."""
    from fastapi.testclient import TestClient

    main = env
    h = {"Authorization": "Bearer test-token"}
    with TestClient(main.app) as client:
        r = client.get("/housekeeping/status", headers=h)
        assert r.status_code == 200, r.text
        hk = r.json()
        assert hk["hk_version"] == "0.1.0" and set(hk["watermarks"]) >= {"capture", "raw", "ingest", "extract", "geo", "resolve", "snapshot", "publish", "retention"}
        assert hk["watermarks"]["resolve"]["enabled"] is True and hk["watermarks"]["extract"]["records_pending"] == 0
        assert hk["reconciliation"]["R-EXTR"]["ratio"] == 1.0 and hk["reconciliation"]["R-RES"]["ratio"] == 1.0
        assert hk["health"]["extract"] == "healthy" and hk["health"]["resolve"] == "healthy"
        # fixture rows were inserted straight into records (no payload hash, no capture events) → the expected findings
        types = {f["finding_type"] for f in hk["findings"]}
        assert "RECORD_WITHOUT_EVENT" in types and hk["reconciliation"]["R-EVID"]["ratio"] is None and all(f["status"] == "OPEN" for f in hk["findings"])
        assert "lifecycle" in hk["watermarks"]["snapshot"] and sum(hk["watermarks"]["snapshot"]["lifecycle"].values()) >= 2
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "housekeeping_status", "arguments": {}}})
        assert mcp.status_code == 200 and "R-EVID" in mcp.text


def test_housekeeping_persists_runs_findings_and_lineage_walks(env):
    """HK-001 §5/§8/§10: a run persists watermarks, checks and findings; a repeat run bumps last_seen; a finding that
    disappears is RESOLVED (row kept); lineage answers from every identifier shape with versions and counts only."""
    import asyncpg
    from fastapi.testclient import TestClient

    main = env
    h = {"Authorization": "Bearer test-token"}
    with TestClient(main.app) as client:
        r1 = client.post("/admin/housekeeping/run", headers=h).json()
        r2 = client.post("/admin/housekeeping/run", headers=h).json()
        assert r1["run_id"] < r2["run_id"] and r2["findings_open"] >= 1
        f = client.get("/housekeeping/findings", headers=h).json()["findings"]
        ev = next(x for x in f if x["finding_type"] == "RECORD_WITHOUT_EVENT")
        assert ev["first_seen_run_id"] == r1["run_id"] and ev["last_seen_run_id"] == r2["run_id"] and ev["status"] == "OPEN"
        st = client.get("/housekeeping/status", headers=h).json()
        assert st["last_run"]["run_id"] == r2["run_id"] and st["last_run"]["error"] is None
        dry = client.post("/admin/housekeeping/run?dry_run=1", headers=h).json()
        assert dry["dry_run"] is True
        # lineage from a record key, an observation, a property, a decision
        rec = client.get("/lineage/facebook:m1", headers=h).json()
        types = {n["type"] for n in rec["nodes"]}
        assert {"record", "observation", "location", "decision", "market_property", "snapshot"} <= types, types
        assert not any("text" in n or "raw_value" in n for n in rec["nodes"])
        rn = next(n for n in rec["nodes"] if n["type"] == "record")
        assert "sightings" in rn and rn["sighting_count"] == len(rn["sightings"]) and rn["sightings"][0]["context"] == "container:test" if rn["sighting_count"] else True
        obs_id = next(n["id"] for n in rec["nodes"] if n["type"] == "observation" and n.get("current"))
        mp = next(n["id"] for n in rec["nodes"] if n["type"] == "market_property")
        assert client.get(f"/lineage/obs:{obs_id}", headers=h).status_code == 200
        walk = client.get(f"/lineage/{mp}", headers=h).json()
        assert walk["seed"]["type"] == "market_property" and sum(1 for n in walk["nodes"] if n["type"] == "record") >= 4  # the linked advertisers
        dec = next(n["id"] for n in rec["nodes"] if n["type"] == "decision")
        assert client.get(f"/lineage/dec:{dec}", headers=h).json()["seed"]["type"] == "decision"
        assert client.get("/lineage/not-an-id", headers=h).status_code == 404
        assert client.get("/lineage/" + "a" * 64, headers=h).json()["nodes"] == []  # unknown hash → empty walk with note, not 404
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lineage", "arguments": {"id": "facebook:m1"}}})
        assert mcp.status_code == 200 and "market_property" in mcp.text

    async def backfill_and_rerun():
        con = await asyncpg.connect(DSN)
        try:
            # make the RECORD_WITHOUT_EVENT finding disappear: backfill capture events for the fixture rows
            await con.execute("INSERT INTO capture_events (record_key, captured_at, context) SELECT key, captured_at, 'container:test' FROM records WHERE key LIKE 'facebook:m%' ON CONFLICT DO NOTHING")
        finally:
            await con.close()

    asyncio.run(backfill_and_rerun())
    with TestClient(main.app) as client:
        client.post("/admin/housekeeping/run", headers=h)
        hist = client.get("/housekeeping/findings?status=ALL&type=RECORD_WITHOUT_EVENT", headers=h).json()["findings"]
        assert hist and hist[0]["status"] == "RESOLVED" and hist[0]["resolved_run_id"] is not None
        assert client.get("/housekeeping/findings?type=RECORD_WITHOUT_EVENT", headers=h).json()["count"] == 0
