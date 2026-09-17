"""Live extraction test — needs LENS_TEST_DSN (disposable PostgreSQL). Skipped in CI.

Covers 001C §8: run over L1 → extract.* rows; idempotent per (record_key, content_hash, method_version);
force re-run appends; FX from extract.fx_rates; contact raw value encrypted, never in claims JSON;
L1 untouched; HTTP endpoints + MCP tools read it back.
"""
from __future__ import annotations

import asyncio
import os
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
            await con.execute("DELETE FROM extract.contact_sightings; DELETE FROM extract.price_observations; DELETE FROM extract.claims; "
                              "DELETE FROM extract.observations; DELETE FROM extract.runs; DELETE FROM extract.contact_points; DELETE FROM extract.fx_rates;")
            await con.execute("DELETE FROM records WHERE key LIKE 'facebook:x%'")
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
    assert r1["records_in"] == 3 and r1["observations_out"] == 3
    r2 = main.loop.run_until_complete(run(main.extract_store, trigger="test", pgcrypto=main.db.pgcrypto, contact_salt="s"))
    assert r2["records_in"] == 0  # nothing new: same content_hash + method_version
    r3 = main.loop.run_until_complete(run(main.extract_store, trigger="test", force=True, pgcrypto=main.db.pgcrypto, contact_salt="s"))
    assert r3["observations_out"] == 3  # force appends new observations, old ones remain

    async def counts():
        async with main.db.pool.acquire() as con:
            return (await con.fetchval("SELECT count(*) FROM extract.observations"),
                    await con.fetchval("SELECT count(*) FROM extract.current_observations"),
                    await con.fetchval("SELECT count(*) FROM extract.runs"))

    total, current, runs = main.loop.run_until_complete(counts())
    assert total == 6 and current == 3 and runs == 3


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
        assert r.status_code == 200 and {o["record_key"] for o in r.json()["observations"]} == {"facebook:x1", "facebook:x2"}
        r = client.get("/observations/facebook:x1?all=1", headers=h)
        assert r.status_code == 200 and len(r.json()["observations"]) == 2
        assert client.get("/observations/facebook:nope", headers=h).status_code == 404
        s = client.get("/extract/stats", headers=h).json()
        assert s["claims_without_confidence"] == 0 and s["observations"] == 3 and s["by_signal_class"]["NON_PROPERTY"] == 1
        r = client.post("/admin/fx?currency=THB&rate_date=2026-09-01&lak_per_unit=640", headers=h)
        assert r.status_code == 200
        r = client.post("/admin/extract?force=1&limit=1", headers=h)
        assert r.status_code == 200 and r.json()["observations_out"] == 1
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "extraction_stats", "arguments": {}}})
        assert mcp.status_code == 200 and "observations" in mcp.text
        mcp = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_observation", "arguments": {"key": "facebook:x1"}}})
        assert "raw_value" not in mcp.text and "PROPERTY_SALE" in mcp.text
