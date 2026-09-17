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
        self.seen: dict[str, dict] = {}
        self.runs: list[dict] = []

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def upsert_records(self, rows):
        inserted = 0
        for row in rows:
            if row["key"] not in self.store:
                inserted += 1
            else:
                prev = self.store[row["key"]]
                row = {**prev, **{k: v for k, v in row.items() if v is not None}}
            self.store[row["key"]] = row
        return (inserted, len(rows) - inserted)

    async def upsert_seen(self, rows):
        inserted = 0
        for row in rows:
            if row["url_hash"] not in self.seen:
                inserted += 1
            self.seen[row["url_hash"]] = row
        return (inserted, len(rows) - inserted)

    async def seen_since(self, since, limit):
        return list(self.seen.values())[:limit]

    async def record_ingest_run(self, source, ext_version, sent, inserted, updated, remote_addr):
        self.runs.append({"source": source, "sent": sent, "inserted": inserted, "updated": updated})
        return len(self.runs)

    async def count_records(self) -> int:
        return len(self.store)

    async def purge_records(self, retention_days: int):
        self.purged = retention_days
        return {"records": 0 if retention_days <= 0 else 2, "seen_links": 0 if retention_days <= 0 else 1}

    async def purge_raw_bodies(self, days: int):
        self.raw_purged = days
        return 0 if days <= 0 else 3

    raw: dict = {}

    async def upsert_raw(self, captures):
        inserted = 0
        for c in captures:
            if c["payload_hash"] not in self.raw:
                inserted += 1
                self.raw[c["payload_hash"]] = c
        return (inserted, len(captures) - inserted)

    async def provenance(self, key):
        if key not in self.store:
            return None
        return {"key": key, "events": [{"payload_hash": self.store[key].get("first_payload_hash")}]}

    async def provenance_coverage(self):
        return {"records": len(self.store), "coverage": 1.0}


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


def test_ingest_comment_with_parent_and_matched_fields():
    client, fake = make_client()
    h = {"authorization": "Bearer test-token"}
    comment = {
        "key": "facebook:c1", "platform": "facebook", "post_id": "c1",
        "record_type": "comment", "parent_post_id": "123",
        "text": "ລາຄາເທົ່າໃດ", "captured_at": "2026-09-16T00:00:00Z",
        "matched_keywords": ["ລາຄາ"], "match_score": 1, "matched_via": "comment",
        "media": [], "hashtags": [],
    }
    r = client.post("/ingest", json={"records": [comment]}, headers=h)
    assert r.status_code == 200 and r.json()["inserted"] == 1
    stored = fake.store["facebook:c1"]
    assert stored["record_type"] == "comment"
    assert stored["parent_post_id"] == "123"
    assert stored["match_score"] == 1


def test_seen_upsert_and_list():
    client, fake = make_client()
    h = {"authorization": "Bearer test-token"}
    link = {"url_hash": "abc123", "url": "https://www.facebook.com/groups/1/posts/2/", "platform": "facebook", "last_status": "seen"}
    r = client.post("/seen", json={"links": [link]}, headers=h)
    assert r.status_code == 200 and r.json()["inserted"] == 1
    # idempotent
    r2 = client.post("/seen", json={"links": [link]}, headers=h)
    assert r2.json()["inserted"] == 0 and r2.json()["updated"] == 1
    lst = client.get("/seen", headers=h)
    assert lst.status_code == 200 and lst.json()["count"] == 1


def test_seen_requires_token():
    client, _ = make_client()
    assert client.post("/seen", json={"links": []}).status_code == 401


def test_admin_purge_endpoint_runs_raw_then_records():
    c, fake = make_client()
    assert c.post("/admin/purge").status_code == 401
    r = c.post("/admin/purge", headers={"authorization": "Bearer test-token"})
    assert r.status_code == 200 and r.json()["records"] == 2 and fake.purged == main.RETENTION_DAYS
    assert r.json()["raw_bodies_purged"] == 3 and fake.raw_purged == main.RAW_BODY_RETENTION_DAYS
    r = c.post("/admin/purge?days=0&raw_days=0", headers={"authorization": "Bearer test-token"})
    assert r.json() == {"raw_retention_days": 0, "raw_bodies_purged": 0, "retention_days": 0, "records": 0, "seen_links": 0}


def test_raw_ingest_verifies_hash_and_dedupes():
    import hashlib
    c, fake = make_client()
    fake.raw = {}
    body = '{"data":{"x":1}}'
    h = hashlib.sha256(body.encode()).hexdigest()
    cap = {"payload_hash": h, "url": "https://www.facebook.com/api/graphql/", "captured_at": "2026-09-17T00:00:00Z", "body": body, "platform": "facebook"}
    H = {"authorization": "Bearer test-token"}
    assert c.post("/raw", json={"captures": [cap]}).status_code == 401
    r = c.post("/raw", json={"source": "bst-social-lens", "version": "0.7.0", "captures": [cap, cap]}, headers=H)
    assert r.status_code == 200 and r.json() == {"received": 2, "inserted": 1, "duplicate": 1, "rejected": 0, "rejected_hashes": []}
    bad = {**cap, "payload_hash": "0" * 64}
    r = c.post("/raw", json={"captures": [bad]}, headers=H)
    assert r.json()["rejected"] == 1 and r.json()["rejected_hashes"] == ["0" * 64] and r.json()["inserted"] == 0
    assert fake.raw[h]["body_bytes"] == len(body) and fake.raw[h]["ext_version"] == "0.7.0"


def test_ingest_carries_provenance_and_provenance_endpoint():
    c, fake = make_client()
    H = {"authorization": "Bearer test-token"}
    rec = {**REC, "payload_hash": "a" * 64, "content_hash": "b" * 64}
    r = c.post("/ingest", json={"records": [rec]}, headers=H)
    assert r.status_code == 200
    row = fake.store["facebook:123"]
    assert row["first_payload_hash"] == "a" * 64 and row["last_payload_hash"] == "a" * 64 and row["content_hash"] == "b" * 64
    assert c.get("/provenance/facebook:123", headers=H).json()["events"][0]["payload_hash"] == "a" * 64
    assert c.get("/provenance/nope", headers=H).status_code == 404
    assert c.get("/provenance", headers=H).json()["coverage"] == 1.0
