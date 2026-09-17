"""MCP adapter — Model Context Protocol over Streamable HTTP, served at POST /mcp.

Lets BST agents (via BizEra MCP Hub, Claude Code, or any MCP client) query Social
Lens with tools instead of raw REST. Deliberately dependency-free: a small JSON-RPC
2.0 dispatcher that implements the subset every client uses — `initialize`,
`notifications/initialized`, `ping`, `tools/list`, `tools/call`. No server-initiated
SSE stream (GET /mcp answers 405), no resources/prompts. Read-only: every tool is a
query; nothing here writes to LensDB.

Auth: the same bearer token as the REST API (`LENS_API_TOKEN`).
Protocol version: 2025-03-26 (Streamable HTTP). Clients sending an older version
still get a compatible response — the dispatcher does not gate on it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "bst-social-lens", "version": "0.6.7"}

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603

MAX_LIMIT = 200

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_records",
        "description": (
            "Search captured Facebook/TikTok posts and comments in LensDB. Substring match on text "
            "(Lao/Thai/English, case-insensitive) and/or filters. Returns newest first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring to find in post/comment text (e.g. ຂາຍດິນ)."},
                "platform": {"type": "string", "enum": ["facebook", "tiktok"]},
                "record_type": {"type": "string", "enum": ["post", "comment"]},
                "container_id": {"type": "string", "description": "Group id / hashtag / search term the item was seen under."},
                "keyword": {"type": "string", "description": "Only records whose matched_keywords include this term."},
                "matched_only": {"type": "boolean", "description": "Only keyword-matched records (match_score > 0). Default true."},
                "since": {"type": "string", "description": "ISO-8601; only records created/captured after this."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 25},
            },
        },
    },
    {
        "name": "get_record",
        "description": "Fetch one record by key (`platform:post_id`) together with its comments.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "get_stats",
        "description": "Totals: records, matched, seen links, by platform, by type, last capture time.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "top_containers",
        "description": "Groups/hashtags ranked by number of matched records — where the signal is.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": ["facebook", "tiktok"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    },
    {
        "name": "list_seen",
        "description": "Seen-link frontier rows (permalinks already processed), optionally by status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["seen", "queued", "fetched", "failed", "skipped"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 50},
            },
        },
    },
    {
        "name": "list_observations",
        "description": "Phase 6 (001C): current L2 observations — classification per source record (signal class, asset type, confidences). Claims are claims, never facts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "signal_class": {"type": "string", "enum": ["PROPERTY_SALE", "PROPERTY_RENT", "PROPERTY_WANTED", "AGENT_ADVERTISEMENT", "DEVELOPER_PROJECT", "PRICE_DISCUSSION", "MARKET_INFORMATION", "NON_PROPERTY", "UNCERTAIN"]},
                "asset_type": {"type": "string"},
                "since": {"type": "string", "description": "ISO-8601 lower bound on observed_at"},
                "min_conf": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 50},
            },
        },
    },
    {
        "name": "get_observation",
        "description": "Current observation for one source record with all its claims (field, value text, evidence span, method, confidence, review status) and price observations. Contact raw values are never returned.",
        "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "all": {"type": "boolean", "default": False}}, "required": ["key"]},
    },
    {
        "name": "extraction_stats",
        "description": "Extraction KPIs: observations by signal class / asset type, UNCERTAIN share, claims without confidence (must be 0), price observations lacking FX, recent runs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "geo_stats",
        "description": "Phase 6 (001D): current admin version, gazetteer size, precision distribution of primary resolved locations, unresolved share, precision_assigned_share (must be 1.0).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_text",
        "description": "Dry-run the location resolver over a text (no write): location claims found and their resolutions with precision, admin codes, confidence and signals.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "quality_stats",
        "description": "Phase 8 (001G): DQ grade distribution (A–D), mean score, share B-or-better, validation exception counts over current property observations.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "housekeeping_findings",
        "description": "SLL-DATA-HK-001 §8: persisted Housekeeper findings (type, severity, count, expected/observed state, recommended action, auto_action_allowed, status). Counts and ids only.",
        "inputSchema": {"type": "object", "properties": {"type": {"type": "string"}, "status": {"type": "string", "enum": ["OPEN", "ACTIONED", "REVIEW", "RESOLVED", "SUPPRESSED", "ALL"]}, "severity": {"type": "string", "enum": ["INFO", "WARN", "ERROR", "CRITICAL"]}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 50}}},
    },
    {
        "name": "lineage",
        "description": "SLL-DATA-HK-001 §5: lineage of one identifier — payload hash (64-hex), record key (platform:post_id), obs:<id>, loc:<id>, dec:<id>, MP-…, snap:<id> — as nodes with producing job/version/run and edges, upstream and downstream. No record text or contacts.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "housekeeping_status",
        "description": "SLL-DATA-HK-001 (read-only): per-stage watermarks (capture, raw, ingest, extract, geo, resolve, snapshot, publish, retention), reconciliation ratios (R-EVID, R-SIGHT, R-EXTR, R-GEO, R-RES, R-PROP, R-SNAP, R-RET), stage health and open findings with recommended actions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_market_properties",
        "description": "Phase 7 (001E, only when resolution is enabled): active market properties with latest statistics snapshot. Prices are OBSERVED_ASKING statistics, never a single value.",
        "inputSchema": {"type": "object", "properties": {"district": {"type": "string"}, "village": {"type": "string"}, "asset_type": {"type": "string"}, "state": {"type": "string", "enum": ["CLEAN", "PENDING_REVIEW", "DISPUTED"]}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 25}}},
    },
    {
        "name": "get_market_property",
        "description": "One market property: statistics snapshot, current observations with their decision and match signals, price observations, decision history.",
        "inputSchema": {"type": "object", "properties": {"market_property_id": {"type": "string"}}, "required": ["market_property_id"]},
    },
    {
        "name": "resolution_stats",
        "description": "Resolution KPIs: properties, multi-observation properties, clusters, decisions by state, review-queue p95 age, unexplained machine decisions (must be 0), recent runs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

ToolFn = Callable[[dict[str, Any]], Awaitable[Any]]


def _clamp_limit(args: dict[str, Any], default: int, maximum: int = MAX_LIMIT) -> int:
    try:
        n = int(args.get("limit", default))
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer")
    return max(1, min(n, maximum))


def _parse_since(v: Any) -> datetime | None:
    """ISO-8601 → aware datetime (asyncpg needs a datetime, not a string)."""
    if v in (None, ""):
        return None
    if not isinstance(v, str):
        raise ValueError("since must be an ISO-8601 string")
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"since is not ISO-8601: {v}") from e
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _json_default(o: Any) -> Any:
    # asyncpg returns datetime/Record-like values; make them serialisable.
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


class McpDispatcher:
    """Binds tool implementations to a Database-like object (duck-typed for tests)."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._tools: dict[str, ToolFn] = {
            "search_records": self.search_records,
            "get_record": self.get_record,
            "get_stats": self.get_stats,
            "top_containers": self.top_containers,
            "list_seen": self.list_seen,
            "list_observations": self.list_observations,
            "get_observation": self.get_observation,
            "extraction_stats": self.extraction_stats,
            "geo_stats": self.geo_stats,
            "resolve_text": self.resolve_text,
            "quality_stats": self.quality_stats,
            "housekeeping_status": self.housekeeping_status,
            "housekeeping_findings": self.housekeeping_findings,
            "lineage": self.lineage,
            "search_market_properties": self.search_market_properties,
            "get_market_property": self.get_market_property,
            "resolution_stats": self.resolution_stats,
        }

    # ---------- tools ----------

    async def search_records(self, a: dict[str, Any]) -> Any:
        return await self.db.search_records(
            query=a.get("query"),
            platform=a.get("platform"),
            record_type=a.get("record_type"),
            container_id=a.get("container_id"),
            keyword=a.get("keyword"),
            matched_only=bool(a.get("matched_only", True)),
            since=_parse_since(a.get("since")),
            limit=_clamp_limit(a, 25),
        )

    async def get_record(self, a: dict[str, Any]) -> Any:
        key = a.get("key")
        if not isinstance(key, str) or ":" not in key:
            raise ValueError("key must look like platform:post_id")
        return await self.db.get_record_with_comments(key)

    async def get_stats(self, a: dict[str, Any]) -> Any:
        return await self.db.stats()

    async def top_containers(self, a: dict[str, Any]) -> Any:
        return await self.db.top_containers(platform=a.get("platform"), limit=_clamp_limit(a, 10, 50))

    async def list_seen(self, a: dict[str, Any]) -> Any:
        return await self.db.seen_list(status=a.get("status"), limit=_clamp_limit(a, 50))

    async def list_observations(self, a: dict[str, Any]) -> Any:
        try:
            min_conf = float(a.get("min_conf", 0.0))
        except (TypeError, ValueError):
            raise ValueError("min_conf must be a number")
        return await self.db.extract.list_observations(a.get("signal_class"), a.get("asset_type"), _parse_since(a.get("since")), max(0.0, min(1.0, min_conf)), _clamp_limit(a, 50))

    async def get_observation(self, a: dict[str, Any]) -> Any:
        key = a.get("key")
        if not isinstance(key, str) or ":" not in key:
            raise ValueError("key must look like platform:post_id")
        return await self.db.extract.get_observation(key, all_runs=bool(a.get("all", False)))

    async def extraction_stats(self, a: dict[str, Any]) -> Any:
        return await self.db.extract.stats()

    async def geo_stats(self, a: dict[str, Any]) -> Any:
        return await self.db.geo.stats()

    async def resolve_text(self, a: dict[str, Any]) -> Any:
        text = a.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        from .extract.rules import extract_observation
        from .geo.resolver import resolve

        obs = extract_observation(text[:4000])
        gaz = await self.db.geo.gazetteer()
        return {"admin_version": gaz.admin_version, "resolutions": [r.__dict__ for r in resolve(obs.claims, gaz)]}

    async def quality_stats(self, a: dict[str, Any]) -> Any:
        from .extract.store import quality_stats

        return await quality_stats(self.db)

    async def housekeeping_status(self, a: dict[str, Any]) -> Any:
        from . import main as m
        from .housekeeping import housekeeping_status

        return await housekeeping_status(self.db, **m._hk_config())

    async def housekeeping_findings(self, a: dict[str, Any]) -> Any:
        from .housekeeping.store import list_findings

        return await list_findings(self.db, ftype=a.get("type"), status=a.get("status"), severity=a.get("severity"), limit=_clamp_limit(a, 50))

    async def lineage(self, a: dict[str, Any]) -> Any:
        from .housekeeping.lineage import lineage

        res = await lineage(self.db, str(a.get("id", "")))
        if res is None:
            raise ValueError("unknown identifier")
        return res

    def _res(self) -> Any:
        r = getattr(self.db, "resolution", None)
        if r is None:
            raise ValueError("resolution is disabled (LENS_RESOLUTION_ENABLED=0)")
        return r

    async def search_market_properties(self, a: dict[str, Any]) -> Any:
        return await self._res().list_properties(a.get("district"), a.get("village"), a.get("asset_type"), a.get("state"), _clamp_limit(a, 25))

    async def get_market_property(self, a: dict[str, Any]) -> Any:
        mp = a.get("market_property_id")
        if not isinstance(mp, str) or not mp.startswith("MP-"):
            raise ValueError("market_property_id must look like MP-…")
        res = await self._res().get_property(mp)
        if res is None:
            raise ValueError("unknown market property")
        return res

    async def resolution_stats(self, a: dict[str, Any]) -> Any:
        return await self._res().stats()

    # ---------- JSON-RPC ----------

    async def handle(self, body: Any) -> tuple[int, Any]:
        """Returns (http_status, json_body_or_None). Notifications → (202, None)."""
        if isinstance(body, list):  # batch
            results = []
            for item in body:
                status, res = await self._one(item)
                if res is not None:
                    results.append(res)
            return (200, results) if results else (202, None)
        return await self._one(body)

    async def _one(self, msg: Any) -> tuple[int, Any]:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return 200, _err(None, INVALID_REQUEST, "invalid JSON-RPC request")
        method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        is_notification = "id" not in msg
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "Read-only access to BST Social Lens (LensDB). Use search_records for leads, get_stats for KPIs.",
                }
            elif method == "notifications/initialized" or method.startswith("notifications/"):
                return 202, None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name = params.get("name")
                fn = self._tools.get(name)
                if not fn:
                    return 200, _err(rid, INVALID_PARAMS, f"unknown tool: {name}")
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    return 200, _err(rid, INVALID_PARAMS, "arguments must be an object")
                try:
                    data = await fn(args)
                except ValueError as e:  # bad input → tool error, not protocol error
                    result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
                else:
                    text = json.dumps(data, ensure_ascii=False, default=_json_default)
                    # structuredContent must be JSON-native (datetimes/Decimals from asyncpg are not): round-trip through the text form.
                    result = {"content": [{"type": "text", "text": text}], "structuredContent": {"result": json.loads(text)} if isinstance(data, (dict, list)) else None}
                    if result["structuredContent"] is None:
                        del result["structuredContent"]
            else:
                return 200, _err(rid, METHOD_NOT_FOUND, f"method not found: {method}")
        except Exception as e:  # never leak a stack trace to the client
            return 200, _err(rid, INTERNAL, f"{type(e).__name__}: {e}")
        if is_notification:
            return 202, None
        return 200, {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}
