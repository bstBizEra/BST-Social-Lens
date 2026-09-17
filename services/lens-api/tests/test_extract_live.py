"""Live extraction test — needs LENS_TEST_DSN (disposable PostgreSQL). Skipped in CI.

Covers 001C §8: run over L1 → extract.* rows; idempotent per (record_key, content_hash, method_version);
force re-run appends; FX from extract.fx_rates; contact raw value encrypted, never in claims JSON;
L1 untouched; HTTP endpoints + MCP tools read it back.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

DSN = os.environ.get("LENS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="LENS_TEST_DSN not set")

os.environ.setdefault("LENS_API_TOKEN", "test-token")
os.environ["LENS_CONTACT_KEY"] = "test-contact-key"
os.environ["LENS_EXTRACT_INTERVAL_MIN"] = "0"

POSTS = [
    ("facebook:x1", "ຂາຍດິນ 20x30 ບ້ານນາສ້າງໄຜ່ ເມືອງໄຊທານີ ລາຄາ 2.5 ຕື້ ໂທ 020 5512 3456"),
    ("facebook:x2", "Land for sale 600 sqm $45,000 owner direct"),
    ("facebook:x3", "ສະບາຍດີ ມື້ນີ້ອາກາດດີ"),
    ("facebook:x4", "ໃຫ້ເຊົ່າເຮືອນ ບ້ານໂພນຕ້ອງ ລາຄາ 350"),   # ambiguous village + bare price → review queues
    ("facebook:x5", "ຂາຍດິນ 20x30 https://maps.google.com/?q=18.052,102.661 ລາຄາ 1.5 ຕື້"),  # pin inside the V-XTN-DDK test polygon
]


@pytest.fixture(scope="module")
def env():
    from app import main
    from app.extract.service import run_extraction

    async def setup():
        main.DSN = DSN  # noqa: F841 — db object already bound; rebind its DSN
        main.db._dsn = DSN
        await main.db.connect()
        async with main.db.pool.acquire() as con:
            if await con.fetchval("SELECT to_regclass('market.properties') IS NOT NULL"):  # 001E suite ran on this DB (flag on)
                await con.execute("""DELETE FROM market.property_stats_snapshots; DELETE FROM resolution.entity_decisions; DELETE FROM resolution.entity_candidates;
                                     DELETE FROM resolution.property_transitions; DELETE FROM market.properties; DELETE FROM resolution.cluster_edges;
                                     DELETE FROM resolution.cluster_members; DELETE FROM resolution.listing_clusters; DELETE FROM resolution.runs""")
            await con.execute("DELETE FROM housekeeping.actions; DELETE FROM housekeeping.findings; DELETE FROM housekeeping.checks; DELETE FROM housekeeping.watermarks; DELETE FROM housekeeping.lifecycle_states; DELETE FROM housekeeping.runs")
            await con.execute("DELETE FROM audit.events; DELETE FROM geo.resolved_locations; DELETE FROM extract.contact_sightings; DELETE FROM extract.price_observations; DELETE FROM extract.claims; "
                              "DELETE FROM extract.observations; DELETE FROM extract.runs; DELETE FROM extract.contact_points; DELETE FROM extract.fx_rates;")
            await con.execute("DELETE FROM records WHERE key LIKE 'facebook:x%' OR key LIKE 'facebook:m%'")  # m* = 001E live suite
            for key, text in POSTS:
                await con.execute(
                    "INSERT INTO records (key, platform, post_id, record_type, text, captured_at, created_at, content_hash) VALUES ($1,'facebook',$2,'post',$3,$4,$5,$6)",
                    key, key.split(":")[1], text, datetime.now(timezone.utc), datetime(2026, 9, 10, tzinfo=timezone.utc), "h-" + key,
                )
        await main.extract_store.upsert_fx("USD", datetime(2026, 9, 8).date(), Decimal("21500"), "MANUAL")
        return main, run_extraction

    loop = asyncio.new_event_loop()  # one loop for the module: the asyncpg pool is bound to it
    main, run = loop.run_until_complete(setup())  # noqa: F811 — same module object, rebound for clarity
    main.loop = loop
    yield main, run
    loop.run_until_complete(main.db.close())
    loop.close()


def test_run_extracts_and_is_idempotent(env):
    main, run = env
    r1 = main.loop.run_until_complete(run(main.extract_store, trigger="test", pgcrypto=main.db.pgcrypto, contact_salt="s"))
    assert r1["records_in"] == 5 and r1["observations_out"] == 5
    r2 = main.loop.run_until_complete(run(main.extract_store, trigger="test", pgcrypto=main.db.pgcrypto, contact_salt="s"))
    assert r2["records_in"] == 0  # nothing new: same content_hash + method_version
    r3 = main.loop.run_until_complete(run(main.extract_store, trigger="test", force=True, pgcrypto=main.db.pgcrypto, contact_salt="s"))
    assert r3["observations_out"] == 5  # force appends new observations, old ones remain

    async def counts():
        async with main.db.pool.acquire() as con:
            return (await con.fetchval("SELECT count(*) FROM extract.observations"),
                    await con.fetchval("SELECT count(*) FROM extract.current_observations"),
                    await con.fetchval("SELECT count(*) FROM extract.runs"))

    total, current, runs = main.loop.run_until_complete(counts())
    assert total == 10 and current == 5 and runs == 3


def test_observation_content_fx_and_contact_protection(env):
    main, _ = env
    o = main.loop.run_until_complete(main.extract_store.get_observation("facebook:x1"))
    assert o["signal_class"] == "PROPERTY_SALE" and o["asset_type"] == "LAND"
    fields = {c["field"] for c in o["claims"]}
    assert {"PRICE", "AREA", "LOCATION_TEXT", "CONTACT", "TRANSACTION_TYPE", "ASSET_TYPE"} <= fields
    for c in o["claims"]:
        assert "raw_value" not in c["normalised"] and c["confidence"] is not None and c["span_end"] > c["span_start"]
    (p,) = o["price_observations"]
    assert p["price_type"] == "ASKING_SALE" and p["amount_lak"] == "2500000000" and p["fx_source"] == "IDENTITY"

    o2 = main.loop.run_until_complete(main.extract_store.get_observation("facebook:x2"))
    (p2,) = o2["price_observations"]
    assert p2["currency_original"] == "USD" and p2["amount_lak"] == "967500000" and p2["fx_source"] == "MANUAL" and p2["price_per_sqm_lak"] == "1612500"

    async def contact():
        async with main.db.pool.acquire() as con:
            row = await con.fetchrow("SELECT kind, masked_value, sightings, pgp_sym_decrypt(raw_value_enc, $1) AS raw FROM extract.contact_points", "test-contact-key")
            l1 = await con.fetchrow("SELECT text, content_hash FROM records WHERE key='facebook:x1'")
            return dict(row), dict(l1)

    c, l1 = main.loop.run_until_complete(contact())
    assert c["kind"] == "PHONE" and c["masked_value"] == "0205XXXXX56" and c["raw"] == "02055123456" and c["sightings"] == 2
    assert l1["text"].startswith("ຂາຍດິນ") and l1["content_hash"] == "h-facebook:x1"  # L1 untouched


def test_http_and_mcp_read_paths(env):
    main, _ = env
    from fastapi.testclient import TestClient

    h = {"Authorization": "Bearer test-token"}
    with TestClient(main.app) as client:
        r = client.get("/observations?class=PROPERTY_SALE", headers=h)
        assert r.status_code == 200 and {o["record_key"] for o in r.json()["observations"]} == {"facebook:x1", "facebook:x2", "facebook:x5"}
        r = client.get("/observations/facebook:x1?all=1", headers=h)
        assert r.status_code == 200 and len(r.json()["observations"]) == 2
        assert client.get("/observations/facebook:nope", headers=h).status_code == 404
        s = client.get("/extract/stats", headers=h).json()
        assert s["claims_without_confidence"] == 0 and s["observations"] == 5 and s["by_signal_class"]["NON_PROPERTY"] == 1
        r = client.post("/admin/fx?currency=THB&rate_date=2026-09-01&lak_per_unit=640", headers=h)
        assert r.status_code == 200
        r = client.post("/admin/extract?force=1&limit=1", headers=h)
        assert r.status_code == 200 and r.json()["observations_out"] == 1
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "extraction_stats", "arguments": {}}})
        assert mcp.status_code == 200 and "observations" in mcp.text
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_observation", "arguments": {"key": "facebook:x1"}}})
        assert "raw_value" not in mcp.text and "PROPERTY_SALE" in mcp.text


def test_geo_import_resolve_and_stats(env):
    """001D live: import the sample gazetteer, force a run, locations land with the observation; endpoints + MCP read them."""
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    main, run = env
    fixture = json.loads((Path(__file__).parent / "fixtures" / "geo" / "admin-sample.json").read_text(encoding="utf-8"))
    h = {"Authorization": "Bearer test-token"}
    async def clean():  # own connection: the previous TestClient's lifespan closed the shared pool
        from app.db import Database

        d = Database(DSN)
        await d.connect()
        async with d.pool.acquire() as con:
            await con.execute("DELETE FROM geo.resolved_locations; DELETE FROM geo.name_aliases; DELETE FROM geo.villages; DELETE FROM geo.districts; DELETE FROM geo.provinces; DELETE FROM geo.admin_versions;")
        await d.close()

    asyncio.run(clean())
    main.geo_store._gaz = None
    with TestClient(main.app) as client:
        r = client.post("/admin/geo/import?source_ref=fixture", headers=h, json=fixture)
        assert r.status_code == 200 and r.json()["villages"] == 8 and r.json()["is_current"] is True
        assert client.post("/admin/geo/import", headers=h, json=fixture).status_code == 409  # immutable versions
        r = client.get("/geo/resolve", headers=h, params={"text": "ຂາຍດິນ ບ້ານດົງໂດກ https://maps.google.com/?q=18.052,102.661"})
        assert r.status_code == 200 and r.json()["resolutions"][0]["village_code"] == "V-XTN-DDK"
        r = client.post("/admin/extract?force=1", headers=h)
        assert r.status_code == 200 and r.json()["observations_out"] == 5
        o = client.get("/observations/facebook:x1", headers=h).json()
        assert o["locations"] and o["locations"][0]["is_primary"] and o["locations"][0]["precision"] == "VILLAGE"
        assert o["locations"][0]["village_code"] == "V-XTN-NSP" and o["locations"][0]["admin_version"] == "sample-2026.09"
        assert isinstance(o["signal_confidence"], float) and len(str(o["signal_confidence"]).split(".")[1]) <= 4  # rounded REAL
        s = client.get("/geo/stats", headers=h).json()
        assert s["admin_version"]["admin_version"] == "sample-2026.09" and s["gazetteer"]["villages"] == 8
        if s["postgis"]:  # G1 present on this DB: the pin in x5 must resolve through ST_Within, not the centroid approximation
            o5 = client.get("/observations/facebook:x5", headers=h).json()
            pin = next(loc for loc in o5["locations"] if loc["point_source"] == "MAP_URL")
            assert pin["village_code"] == "V-XTN-DDK" and pin["district_code"] == "D-VTE-XTN" and pin["province_code"] == "P-VTE"
            assert any(sig.startswith("st_within:") for sig in pin["signals"]) and "no_polygons" not in pin["signals"]
        assert s["precision_assigned_share"] == 1.0 and s["without_confidence"] == 0 and s["by_precision"]["VILLAGE"] == 1
        r = client.post("/admin/geo/alias?level=village&code=V-XTN-DDK&alias=ດົງໂດກໃຫຍ່", headers=h)
        assert r.status_code == 200
        r = client.get("/geo/resolve", headers=h, params={"text": "ຂາຍດິນ ບ້ານດົງໂດກໃຫຍ່"})
        assert r.json()["resolutions"][0]["village_code"] == "V-XTN-DDK"
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "resolve_text", "arguments": {"text": "ຂາຍດິນ ເມືອງປາກເຊ"}}})
        assert mcp.status_code == 200 and "D-CPS-PKS" in mcp.text
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "geo_stats", "arguments": {}}})
        assert mcp.status_code == 200 and "precision_assigned_share" in mcp.text


def test_review_csv_round_trip_and_quality_stats(env):
    """001G §6 CSV protocol on the extraction + location queues; audit rows; /quality/stats."""
    import csv
    import subprocess
    from pathlib import Path

    from fastapi.testclient import TestClient

    main, _ = env
    out = Path("/var/tmp/review-test")
    envv = {**os.environ, "LENS_DB_DSN": DSN}
    for q in ("extraction", "location"):
        r = subprocess.run([sys.executable, "scripts/review.py", "export", "--queue", q, "--limit", "50", "--out", str(out)], capture_output=True, text=True, env=envv, cwd=Path(__file__).parents[1])
        assert r.returncode == 0, r.stderr
    ext = list(csv.DictReader((out / "review-extraction.csv").open(encoding="utf-8")))
    loc = list(csv.DictReader((out / "review-location.csv").open(encoding="utf-8")))
    assert any(row["record_key"] == "facebook:x4" and row["field"] == "PRICE" for row in ext)      # bare "350" is LOW_CONFIDENCE
    assert any(row["record_key"] == "facebook:x4" and row["precision"] == "TEXT_ONLY" for row in loc)  # ambiguous ໂພນຕ້ອງ
    assert all("raw_value" not in row["normalised"] for row in ext) and all("0205" not in row["context"] for row in ext)  # masked
    # label: correct the price, confirm the location; one bad action to be skipped
    for row in ext:
        if row["record_key"] == "facebook:x4" and row["field"] == "PRICE":
            row["action"], row["corrected_value"], row["reason"] = "CORRECT", "350000000", "350 ລ້ານ implied"
    ext[0]["action"] = ext[0]["action"] or "BOGUS"
    for row in loc:
        if row["record_key"] == "facebook:x4":
            row["action"], row["corrected_value"] = "CORRECT", "precision=DISTRICT;district_code=D-VTE-XST"
    with (out / "review-extraction.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(ext[0].keys()))
        w.writeheader()
        w.writerows(ext)
    with (out / "review-location.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(loc[0].keys()))
        w.writeheader()
        w.writerows(loc)
    for name in ("review-extraction.csv", "review-location.csv"):
        dry = subprocess.run([sys.executable, "scripts/review.py", "import", "--file", str(out / name), "--reviewer", "vily", "--dry-run"], capture_output=True, text=True, env=envv, cwd=Path(__file__).parents[1])
        assert dry.returncode == 0 and "dry-run" in dry.stdout, dry.stderr
        real = subprocess.run([sys.executable, "scripts/review.py", "import", "--file", str(out / name), "--reviewer", "vily"], capture_output=True, text=True, env=envv, cwd=Path(__file__).parents[1])
        assert real.returncode == 0 and "imported" in real.stdout, real.stderr + real.stdout
        again = subprocess.run([sys.executable, "scripts/review.py", "import", "--file", str(out / name), "--reviewer", "vily"], capture_output=True, text=True, env=envv, cwd=Path(__file__).parents[1])
        assert "already reviewed" in again.stdout  # supersede guard
    async def check():  # own connection: the previous TestClient's lifespan closed the shared pool
        from app.db import Database

        d = Database(DSN)
        await d.connect()
        async with d.pool.acquire() as con:
            hc = await con.fetch("SELECT field, value_text, normalised, review_status, reviewer, supersedes_claim_id FROM extract.claims WHERE extraction_method='HUMAN'")
            hl = await con.fetch("SELECT precision, district_code, review_status, supersedes_id, is_primary FROM geo.resolved_locations WHERE resolver_method='HUMAN'")
            au = await con.fetch("SELECT queue, action, batch_id FROM audit.events ORDER BY event_id")
            orig = await con.fetchval("SELECT review_status FROM extract.claims WHERE claim_id=$1", hc[0]["supersedes_claim_id"])
        await d.close()
        return [dict(x) for x in hc], [dict(x) for x in hl], [dict(x) for x in au], orig
    hc, hl, au, orig = asyncio.run(check())
    assert len(hc) == 1 and hc[0]["review_status"] == "CORRECTED" and hc[0]["reviewer"] == "vily" and '"350000000"' in hc[0]["normalised"]
    assert orig == "UNREVIEWED"  # machine row untouched (0.35 is mid-confidence, queued by the < 0.5 rule)
    assert len(hl) == 1 and hl[0]["precision"] == "DISTRICT" and hl[0]["district_code"] == "D-VTE-XST" and hl[0]["review_status"] == "CORRECTED"
    assert len(au) == 2 and {a["queue"] for a in au} == {"extraction", "location"} and len({a["batch_id"] for a in au}) == 2
    with TestClient(main.app) as client:
        h = {"Authorization": "Bearer test-token"}
        s = client.get("/quality/stats", headers=h).json()
        assert s["dq_version"] and s["observations"] == 4 and sum(s["by_grade"].values()) == 4 and "mean_score" in s
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "quality_stats", "arguments": {}}})
        assert mcp.status_code == 200 and "by_grade" in mcp.text


def test_housekeeping_status_without_resolution(env):
    """HK-001 with the flag off: resolve/snapshot/publish report enabled=false, everything else still reconciles."""
    from fastapi.testclient import TestClient

    main, _ = env
    h = {"Authorization": "Bearer test-token"}
    with TestClient(main.app) as client:
        hk = client.get("/housekeeping/status", headers=h).json()
        if not main.RESOLUTION_ENABLED:  # suite may run with the flag on when sharing a DB with the resolution suite
            assert hk["watermarks"]["resolve"] == {"enabled": False} and hk["health"]["resolve"] == "disabled"
        # fixture rows bypass /ingest, so no capture events: the check must report it (health failing) rather than hide it
        assert hk["reconciliation"]["R-SIGHT"]["ratio"] == 0.0 and hk["health"]["capture"] == "failing"
        assert any(f["finding_type"] == "RECORD_WITHOUT_EVENT" and f["auto_action_allowed"] for f in hk["findings"])
        assert hk["reconciliation"]["R-EXTR"]["ratio"] == 1.0 and hk["health"]["extract"] == "healthy"
        # RAW_MISSING is auto-allowed (MARK_RAW_NEEDED): a run marks the hashes and /raw/needed serves them
        r = client.post("/admin/housekeeping/run", headers=h).json()
        if any(f["finding_type"] == "RAW_MISSING" for f in hk["findings"]):
            assert any(a["action"] == "MARK_RAW_NEEDED" and a["result"].get("marked", 0) >= 1 for a in r["actions"]), r
            assert client.get("/raw/needed", headers=h).json()["count"] >= 1
        assert hk["watermarks"]["retention"]["records_pending"] == 0
