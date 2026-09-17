"""Live DDL test — runs only when LENS_TEST_DSN points at a disposable PostgreSQL (CI does not set it)."""
from __future__ import annotations

import asyncio
import os

import pytest

DSN = os.environ.get("LENS_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="LENS_TEST_DSN not set")


def test_init_db_applies_core_and_l2_idempotently():
    from app.db import Database

    async def run():
        db = Database(DSN)
        await db.connect()
        await db.init_db()  # second application must be a no-op
        async with db.pool.acquire() as con:
            tables = {r["table_name"] for r in await con.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema='extract'")}
            l1 = {r["column_name"] for r in await con.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='records'")}
        await db.close()
        return tables, l1, db.pgcrypto

    tables, l1, pgcrypto = asyncio.run(run())
    assert {"runs", "observations", "claims", "price_observations", "fx_rates", "contact_points", "contact_sightings", "current_observations"} <= tables
    assert not ({"signal_class", "asset_type", "confidence"} & l1)  # L2 never leaks into L1
    assert pgcrypto is True
