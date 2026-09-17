"""run_extraction orchestration with a fake store (no DB) + MCP tool registration for the L2 read tools."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.extract.service import run_extraction
from app.mcp import TOOLS, McpDispatcher


class FakeStore:
    def __init__(self, rows):
        self.rows, self.runs, self.written, self.fx_calls = rows, [], [], []

    async def start_run(self, trigger, method_set, method_version, kg):
        self.runs.append({"trigger": trigger, "method_set": method_set, "method_version": method_version, "kg": kg})
        return len(self.runs)

    async def finish_run(self, run_id, records_in, observations_out, claims_out, error=None):
        self.runs[run_id - 1].update(records_in=records_in, observations_out=observations_out, claims_out=claims_out, error=error)

    async def candidates(self, since, limit, force, method_version):
        return self.rows[:limit]

    async def fx_lookup(self, currency, date_iso):
        self.fx_calls.append((currency, date_iso))
        return (Decimal("21500"), "2026-09-08", "MANUAL") if currency == "USD" else None

    async def insert_observation(self, run_id, rec, obs, pgcrypto, resolutions=None, geo=None, admin_version=None):
        self.written.append((run_id, rec["key"], obs))
        return len(self.written), len(obs.claims)


ROWS = [
    {"key": "facebook:1", "record_type": "post", "text": "Land for sale 600 sqm $45,000", "author_name": None,
     "created_at": datetime(2026, 9, 10, tzinfo=timezone.utc), "captured_at": datetime.now(timezone.utc), "content_hash": "a"},
    {"key": "facebook:2", "record_type": "comment", "text": "ລາຄາເທົ່າໃດ", "author_name": None, "created_at": None,
     "captured_at": datetime.now(timezone.utc), "content_hash": "b"},
]


def test_run_extraction_writes_every_candidate_and_records_the_run():
    store = FakeStore(ROWS)
    res = asyncio.run(run_extraction(store, trigger="admin", limit=10, contact_salt="s"))
    assert res == {"run_id": 1, "records_in": 2, "observations_out": 2, "claims_out": res["claims_out"]}
    assert store.runs[0]["method_set"] == "RULE_V1" and store.runs[0]["error"] is None and store.runs[0]["observations_out"] == 2
    (_, k1, o1), (_, k2, o2) = store.written
    assert k1 == "facebook:1" and o1.signal_class == "PROPERTY_SALE"
    (p,) = o1.price_observations
    assert p.amount_lak == Decimal("967500000") and p.fx_source == "MANUAL"  # FX pre-resolved per post date
    assert ("USD", "2026-09-10") in store.fx_calls and ("THB", "2026-09-10") in store.fx_calls
    assert k2 == "facebook:2" and o2.signal_class == "UNCERTAIN"


def test_run_extraction_records_failure_on_the_run():
    class Boom(FakeStore):
        async def insert_observation(self, *a, **k):
            raise RuntimeError("db down")

    store = Boom(ROWS)
    try:
        asyncio.run(run_extraction(store, trigger="scheduled"))
    except RuntimeError:
        pass
    assert store.runs[0]["error"] == "RuntimeError: db down" and store.runs[0]["observations_out"] == 0


def test_mcp_registers_l2_read_tools():
    names = {t["name"] for t in TOOLS}
    assert {"list_observations", "get_observation", "extraction_stats"} <= names
    d = McpDispatcher(db=None)
    assert {"list_observations", "get_observation", "extraction_stats"} <= set(d._tools)
    status, res = asyncio.run(d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_observation", "arguments": {"key": "bad"}}}))
    assert res["result"]["isError"] is True
