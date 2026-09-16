"""MCP adapter tests — JSON-RPC dispatch, auth, and tool wiring over a fake DB."""
from __future__ import annotations

import json
import os

os.environ["LENS_API_TOKEN"] = "test-token"

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.mcp import METHOD_NOT_FOUND, INVALID_PARAMS, PROTOCOL_VERSION, TOOLS  # noqa: E402

H = {"authorization": "Bearer test-token"}


class FakeDB:
    def __init__(self) -> None:
        self.rows = [
            {"key": "facebook:1", "platform": "facebook", "record_type": "post", "container_id": "g1", "text": "ຂາຍດິນ ວຽງຈັນ", "match_score": 2, "matched_keywords": ["ຂາຍ", "ດິນ"], "created_at": "2026-09-16T01:00:00Z"},
            {"key": "facebook:2", "platform": "facebook", "record_type": "comment", "parent_post_id": "1", "container_id": "g1", "text": "ລາຄາເທົ່າໃດ", "match_score": 1, "matched_keywords": ["ລາຄາ"], "created_at": "2026-09-16T02:00:00Z"},
            {"key": "tiktok:3", "platform": "tiktok", "record_type": "post", "container_id": "tag:x", "text": "hello", "match_score": 0, "matched_keywords": [], "created_at": "2026-09-16T03:00:00Z"},
        ]
        self.calls: list[tuple[str, dict]] = []

    async def connect(self): ...
    async def close(self): ...
    async def count_records(self): return len(self.rows)

    async def search_records(self, **kw):
        self.calls.append(("search_records", kw))
        out = self.rows
        if kw["query"]:
            out = [r for r in out if kw["query"] in r["text"]]
        if kw["platform"]:
            out = [r for r in out if r["platform"] == kw["platform"]]
        if kw["record_type"]:
            out = [r for r in out if r["record_type"] == kw["record_type"]]
        if kw["keyword"]:
            out = [r for r in out if kw["keyword"] in r["matched_keywords"]]
        if kw["matched_only"]:
            out = [r for r in out if r["match_score"] > 0]
        return out[: kw["limit"]]

    async def get_record_with_comments(self, key):
        rec = next((r for r in self.rows if r["key"] == key), None)
        if not rec:
            return None
        return {**rec, "comments": [r for r in self.rows if r.get("parent_post_id") == rec["key"].split(":")[1]]}

    async def stats(self):
        return {"total": len(self.rows), "matched": 2, "seen_links": 0, "by_platform": {"facebook": 2, "tiktok": 1}, "by_type": {"post": 2, "comment": 1}, "last_capture": None}

    async def top_containers(self, *, platform, limit):
        self.calls.append(("top_containers", {"platform": platform, "limit": limit}))
        return [{"platform": "facebook", "container_id": "g1", "container_name": "G", "matched": 2}][:limit]

    async def seen_list(self, *, status, limit):
        return []


def client() -> tuple[TestClient, FakeDB]:
    fake = FakeDB()
    main.db = fake
    return TestClient(main.app), fake


def rpc(c: TestClient, method: str, params=None, id_=1, headers=H):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id_ is not None:
        body["id"] = id_
    return c.post("/mcp", json=body, headers=headers)


def test_auth_required():
    c, _ = client()
    assert rpc(c, "ping", headers={}).status_code == 401
    assert c.get("/mcp", headers=H).status_code == 405


def test_initialize_and_tools_list():
    c, _ = client()
    r = rpc(c, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["protocolVersion"] == PROTOCOL_VERSION and "tools" in res["capabilities"]
    assert rpc(c, "notifications/initialized", id_=None).status_code == 202
    tools = rpc(c, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools] == [t["name"] for t in TOOLS]
    assert all("inputSchema" in t for t in tools)


def test_search_records_tool_filters_and_clamps_limit():
    c, fake = client()
    r = rpc(c, "tools/call", {"name": "search_records", "arguments": {"query": "ດິນ", "platform": "facebook", "limit": 9999}})
    res = r.json()["result"]
    data = json.loads(res["content"][0]["text"])
    assert [d["key"] for d in data] == ["facebook:1"]
    assert res["structuredContent"]["result"][0]["key"] == "facebook:1"
    assert fake.calls[0][1]["limit"] == 200  # clamped
    # since is parsed to an aware datetime for asyncpg; bad values are tool errors
    from datetime import datetime
    rpc(c, "tools/call", {"name": "search_records", "arguments": {"since": "2026-09-01T00:00:00Z"}})
    assert isinstance(fake.calls[-1][1]["since"], datetime) and fake.calls[-1][1]["since"].tzinfo is not None
    assert rpc(c, "tools/call", {"name": "search_records", "arguments": {"since": "yesterday"}}).json()["result"]["isError"] is True
    # matched_only default excludes match_score 0
    data = json.loads(rpc(c, "tools/call", {"name": "search_records", "arguments": {"platform": "tiktok"}}).json()["result"]["content"][0]["text"])
    assert data == []
    data = json.loads(rpc(c, "tools/call", {"name": "search_records", "arguments": {"platform": "tiktok", "matched_only": False}}).json()["result"]["content"][0]["text"])
    assert [d["key"] for d in data] == ["tiktok:3"]


def test_get_record_stats_top_containers():
    c, _ = client()
    rec = json.loads(rpc(c, "tools/call", {"name": "get_record", "arguments": {"key": "facebook:1"}}).json()["result"]["content"][0]["text"])
    assert rec["key"] == "facebook:1" and [x["key"] for x in rec["comments"]] == ["facebook:2"]
    bad = rpc(c, "tools/call", {"name": "get_record", "arguments": {"key": "nope"}}).json()["result"]
    assert bad["isError"] is True
    st = json.loads(rpc(c, "tools/call", {"name": "get_stats"}).json()["result"]["content"][0]["text"])
    assert st["total"] == 3
    top = json.loads(rpc(c, "tools/call", {"name": "top_containers", "arguments": {"limit": 1}}).json()["result"]["content"][0]["text"])
    assert top[0]["container_id"] == "g1"


def test_errors_and_batch():
    c, _ = client()
    assert rpc(c, "nope").json()["error"]["code"] == METHOD_NOT_FOUND
    assert rpc(c, "tools/call", {"name": "delete_everything"}).json()["error"]["code"] == INVALID_PARAMS
    r = c.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}], headers=H)
    assert r.status_code == 200 and [x["id"] for x in r.json()] == [1]
    r = c.post("/mcp", content=b"{not json", headers={**H, "content-type": "application/json"})
    assert r.json()["error"]["code"] == -32700
