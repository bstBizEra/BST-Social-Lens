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
            await con.execute("""DELETE FROM market.property_stats_snapshots; DELETE FROM resolution.entity_decisions; DELETE FROM resolution.entity_candidates;
                                 DELETE FROM resolution.property_transitions; DELETE FROM market.properties; DELETE FROM resolution.cluster_edges;
                                 DELETE FROM resolution.cluster_members; DELETE FROM resolution.listing_clusters; DELETE FROM resolution.runs;
                                 DELETE FROM audit.events; DELETE FROM geo.resolved_locations; DELETE FROM extract.contact_sightings; DELETE FROM extract.price_observations;
                                 DELETE FROM extract.claims; DELETE FROM extract.observations; DELETE FROM extract.runs; DELETE FROM extract.contact_points;
                                 DELETE FROM geo.name_aliases; DELETE FROM geo.villages; DELETE FROM geo.districts; DELETE FROM geo.provinces; DELETE FROM geo.admin_versions;""")
            # isolate from other live suites sharing the throwaway DB (test_extract_live uses facebook:x*)
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
    assert res["records_in"] == 6 and res["decisions"] == 6 and res["clusters"] == 1

    async def read():
        async with main.db.pool.acquire() as con:
            dec = await con.fetch("SELECT d.observation_id, o.record_key, d.market_property_id, d.decision, d.source FROM resolution.current_decisions d JOIN extract.observations o USING (observation_id) ORDER BY 1")
            props = await con.fetch("SELECT market_property_id, status FROM market.properties ORDER BY 1")
            obs_n = await con.fetchval("SELECT count(*) FROM extract.observations")
            price_n = await con.fetchval("SELECT count(*) FROM extract.price_observations")
            clus = await con.fetch("SELECT cluster_id, array_agg(record_key ORDER BY record_key) AS m FROM resolution.cluster_members GROUP BY 1")
            snaps = await con.fetch("SELECT market_property_id, observation_count, advertiser_count, cluster_count, asking_stats::text, review_state, resolution_confidence FROM market.property_stats_snapshots ORDER BY computed_at")
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
    # exactly one seed (the first observation processed opens the property as SEPARATE_CANDIDATE); the other four link HIGH
    linked = [by_key[k]["decision"] for k in sorted(same)]
    assert linked.count("SEPARATE_CANDIDATE") == 1 and linked.count("HIGH_CONFIDENCE_MATCH") == 4, linked
    assert "HIGH_CONFIDENCE_MATCH" in (by_key["facebook:m1"]["decision"], by_key["facebook:m5"]["decision"])  # same cluster forces HIGH
    assert all(d["source"].startswith("MATCH_V") for d in dec)
    # nothing collapsed: 6 observations, 6 price observations, 2 properties, prices 2.3–2.7 all present in the snapshot
    assert obs_n == 6 and price_n == 6 and len(props) == 2 and all(p["status"] == "ACTIVE" for p in props)
    snap = next(s for s in reversed(snaps) if s["market_property_id"] == land_mp)
    stats = json.loads(snap["asking_stats"])["ASKING_SALE"]
    assert snap["observation_count"] == 5 and snap["advertiser_count"] == 4 and snap["cluster_count"] == 1
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
    assert res["decisions"] == 5  # the other five were re-decided (new rows), history grows
    assert after["unexplained_machine_decisions"] == 0 and before["unexplained_machine_decisions"] == 0
    assert after["properties_total"] >= before["properties_total"]  # ids never deleted


def test_http_and_mcp_market_reads(env):
    from fastapi.testclient import TestClient

    main = env
    h = {"Authorization": "Bearer test-token"}
    with TestClient(main.app) as client:
        r = client.get("/market/properties?village=V-XTN-DDK", headers=h)
        assert r.status_code == 200 and r.json()["count"] >= 1
        mp = r.json()["properties"][0]["market_property_id"]
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
