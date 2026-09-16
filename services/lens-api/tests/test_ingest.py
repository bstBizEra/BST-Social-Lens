"""Ingest contract + normalization tests. No live Postgres required — the
Database is replaced with an in-memory fake so the endpoint logic, auth, and
record_to_row flattening are all exercised.
"""
from __future__ import annotations

import os

os.environ["LENS_API_TOKEN"] = "test-token"

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.models import SocialRecord, record_to_row  # noqa: E402


class FakeDB:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.runs: list[dict] = []

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def upsert_records(self, rows):
        inserted = 0
        for row in rows:
            if row["key"] not in self.store:
                inserted += 1
            else:
                # emulate GREATEST(captured_at) + COALESCE engagement refresh
                prev = self.store[row["key"]]
                row = {**prev, **{k: v for k, v in row.items() if v is not None}}
            self.store[row["key"]] = row
        return (inserted, len(rows) - inserted)

    async def record_ingest_run(self, source, ext_version, sent, inserted, updated, remote_addr):
        self.runs.append({"source": source, "sent": sent, "inserted": inserted, "updated": updated})
        return len(self.runs)

    async def count_records(self) -> int:
        return len(self.store)


def make_client() -> tuple[TestClient, FakeDB]:
    fake = FakeDB()
    main.db = fake  # module-level singleton used by the handlers
    return TestClient(main.app), fake


REC = {
    "key": "facebook:123",
    "platform": "facebook",
    "post_id": "123",
    "container_id": "999",
    "container_name": "Test Group",
    "author_name": "A",
    "author_hash": "h",
    "text": "hello #lao",
    "captured_at": "2026-09-16T00:00:00Z",
    "reactions_total": 5,
    "media": [{"kind": "image", "url": "https://x/y.jpg"}],
    "hashtags": ["lao"],
    "parser_version": "0.2.0",
    "synced": 0,
}


def test_health_ok():
    client, _ = make_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ingest_requires_token():
    client, _ = make_client()
    r = client.post("/ingest", json={"records": [REC]})
    assert r.status_code == 401


def test_ingest_inserts_then_updates():
    client, fake = make_client()
    h = {"authorization": "Bearer test-token"}

    r1 = client.post("/ingest", json={"source": "ext", "version": "0.2.0", "records": [REC]}, headers=h)
    assert r1.status_code == 200
    assert r1.json() == {"received": 1, "inserted": 1, "updated": 0, "run_id": 1}

    # Same key again with a higher reaction count → update, not insert.
    rec2 = {**REC, "reactions_total": 12}
    r2 = client.post("/ingest", json={"records": [rec2]}, headers=h)
    body = r2.json()
    assert body["inserted"] == 0 and body["updated"] == 1
    assert fake.store["facebook:123"]["reactions_total"] == 12


def test_ingest_ignores_unknown_fields_and_bad_records():
    client, _ = make_client()
    h = {"authorization": "Bearer test-token"}
    rec = {**REC, "some_future_field": "ignored"}
    r = client.post("/ingest", json={"records": [rec]}, headers=h)
    assert r.status_code == 200
    # A record missing required identity fails validation → 422.
    bad = client.post("/ingest", json={"records": [{"platform": "facebook"}]}, headers=h)
    assert bad.status_code == 422


def test_record_to_row_flattens_media_and_hashtags():
    rec = SocialRecord.model_validate(REC)
    row = record_to_row(rec, "ext", "0.2.0")
    assert row["key"] == "facebook:123"
    assert row["media"] == [{"kind": "image", "url": "https://x/y.jpg"}]
    assert row["hashtags"] == ["lao"]
    assert row["ingest_source"] == "ext"
