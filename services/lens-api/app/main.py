"""BST Social Lens — Ingest API (FastAPI).

The single HTTP boundary between the browser extension / Console and LensDB.
Owns no state itself; validate → upsert → record run.

Endpoints
  GET  /health           liveness + record count
  POST /ingest           receive a batch of SocialRecords (bearer auth); records capture events
  POST /raw              receive L0 raw payloads keyed by sha256 (bearer auth) — Phase 5 provenance
  GET  /provenance/{key} L1 row → capture events → raw captures (bearer auth)
  GET  /provenance       coverage metric (Phase 5 exit criterion)
  GET  /records          paginated read for the Console (bearer auth)
  GET  /stats            per-platform counts (bearer auth)
  POST /mcp              Model Context Protocol (Streamable HTTP, read-only tools; bearer auth)
  POST /admin/purge      run the retention purge now (bearer auth); also runs daily in-process
  POST /admin/extract    run RULE_V1 extraction now (bearer auth); also runs every LENS_EXTRACT_INTERVAL_MIN — Phase 6 (001C)
  GET  /observations     current L2 observations, filterable (bearer auth)
  GET  /observations/{key}  current observation + claims (+ history with ?all=1) (bearer auth)
  GET  /extract/stats    class/asset distribution, claims-without-confidence (must be 0), run history (bearer auth)
  POST /admin/fx         load an FX reference rate (bearer auth) — C2
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal
import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import hashlib

from .db import Database
from .extract.service import run_extraction
from .extract.store import ExtractStore
from .mcp import McpDispatcher
from .models import HealthResult, IngestBody, IngestResult, RawBody, RawResult, SeenBody, record_to_row

DSN = os.environ.get("LENS_DB_DSN", "postgresql://lens:lens@lens-db:5432/lens")
TOKEN = os.environ.get("LENS_API_TOKEN", "")
# Comma-separated origins allowed to call the API from a browser (Console served elsewhere).
CORS_ORIGINS = [o for o in os.environ.get("LENS_CORS_ORIGINS", "").split(",") if o]
# The Social Lens Console static dir; served at / when present (same-origin → no CORS).
CONSOLE_DIR = os.environ.get("LENS_CONSOLE_DIR", "/srv/console")
# Retention (SLL-PROP-DATA-001A D3), ordered L0 body → L1 rows; L2/L3 never; protected rows exempt.
# L0: raw payload BODIES are nulled after N days (hash + capture context are kept forever).
RAW_BODY_RETENTION_DAYS = int(os.environ.get("LENS_RAW_RETENTION_DAYS", "90"))
# L1: records older than N days by post date (else capture date) are deleted unless `protected`.
# 0 disables. Default 730 (24 months). Both run at startup and then every 24 h.
RETENTION_DAYS = int(os.environ.get("LENS_RECORD_RETENTION_DAYS", os.environ.get("LENS_RETENTION_DAYS", "730")))
# Raw payload body size accepted by POST /raw (bytes); larger bodies are rejected, not truncated here.
RAW_MAX_BYTES = int(os.environ.get("LENS_RAW_MAX_BYTES", str(2_000_000)))
PURGE_INTERVAL_S = 24 * 3600
# Phase 6 (001C §8): extraction loop interval in minutes; 0 disables the loop (admin endpoint still works).
EXTRACT_INTERVAL_MIN = int(os.environ.get("LENS_EXTRACT_INTERVAL_MIN", "15"))
EXTRACT_BATCH = int(os.environ.get("LENS_EXTRACT_BATCH", "500"))
# Contact points (D5/C4): salt for contact hashes (stable per deployment) and pgcrypto key for raw values.
CONTACT_SALT = os.environ.get("LENS_CONTACT_SALT") or hashlib.sha256(f"contact-salt:{TOKEN}".encode()).hexdigest()
CONTACT_KEY = os.environ.get("LENS_CONTACT_KEY") or None

log = logging.getLogger("lens-api")

db = Database(DSN)
extract_store = ExtractStore(db, CONTACT_KEY)
db.extract = extract_store  # exposes the L2 store to the MCP dispatcher (read-only tools)
_extract_lock = asyncio.Lock()


async def _extract_once(trigger: str, since=None, limit: int | None = None, force: bool = False) -> dict:
    async with _extract_lock:  # one run at a time; runs append, so overlap would only waste work
        return await run_extraction(extract_store, trigger=trigger, since=since, limit=limit or EXTRACT_BATCH, force=force,
                                    pgcrypto=db.pgcrypto, contact_salt=CONTACT_SALT)


async def _extract_loop() -> None:
    while True:
        try:
            res = await _extract_once("scheduled")
            if res["records_in"]:
                log.info("extraction: %s", res)
        except Exception as e:  # never let extraction take the API down
            log.warning("extraction run failed: %s", e)
        await asyncio.sleep(EXTRACT_INTERVAL_MIN * 60)


async def _purge_loop() -> None:
    while True:
        try:
            n_raw = await db.purge_raw_bodies(RAW_BODY_RETENTION_DAYS)
            res = await db.purge_records(RETENTION_DAYS)
            if n_raw or res["records"] or res["seen_links"]:
                log.info("retention: raw bodies purged=%s (%s d); records=%s (%s d)", n_raw, RAW_BODY_RETENTION_DAYS, res, RETENTION_DAYS)
        except Exception as e:  # never let the purge take the API down
            log.warning("retention purge failed: %s", e)
        await asyncio.sleep(PURGE_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    task = asyncio.create_task(_purge_loop()) if (RETENTION_DAYS > 0 or RAW_BODY_RETENTION_DAYS > 0) else None
    xtask = asyncio.create_task(_extract_loop()) if EXTRACT_INTERVAL_MIN > 0 else None
    yield
    for t in (task, xtask):
        if t:
            t.cancel()
    await db.close()


app = FastAPI(title="BST Social Lens — Ingest API", version="0.6.0", lifespan=lifespan)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["authorization", "content-type"],
    )


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Bearer-token gate. If LENS_API_TOKEN is unset, auth is disabled (dev only)."""
    if not TOKEN:
        return
    expected = f"Bearer {TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@app.get("/health", response_model=HealthResult)
async def health() -> HealthResult:
    try:
        n = await db.count_records()
        return HealthResult(status="ok", db=True, records=n)
    except Exception:
        return HealthResult(status="degraded", db=False)


@app.post("/ingest", response_model=IngestResult, dependencies=[Depends(require_token)])
async def ingest(body: IngestBody, request: Request) -> IngestResult:
    rows = [record_to_row(r, body.source, body.version) for r in body.records]
    inserted, updated = await db.upsert_records(rows)
    run_id = await db.record_ingest_run(
        body.source, body.version, len(rows), inserted, updated,
        request.client.host if request.client else None,
    )
    return IngestResult(received=len(rows), inserted=inserted, updated=updated, run_id=run_id)


@app.get("/records", dependencies=[Depends(require_token)])
async def records(
    platform: str | None = Query(default=None),
    container_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    clauses, args = [], []
    if platform:
        args.append(platform)
        clauses.append(f"platform = ${len(args)}")
    if container_id:
        args.append(container_id)
        clauses.append(f"container_id = ${len(args)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.extend([limit, offset])
    sql = (
        f"SELECT * FROM records {where} "
        f"ORDER BY created_at DESC NULLS LAST LIMIT ${len(args)-1} OFFSET ${len(args)}"
    )
    async with db.pool.acquire() as con:
        rows = await con.fetch(sql, *args)
    return {"count": len(rows), "records": [dict(r) for r in rows]}


@app.post("/seen", dependencies=[Depends(require_token)])
async def seen_upsert(body: SeenBody) -> dict:
    rows = [
        {
            "url_hash": l.url_hash, "url": l.url, "platform": l.platform,
            "last_status": l.last_status, "fetch_count": l.fetch_count, "refresh_after": l.refresh_after,
        }
        for l in body.links
    ]
    inserted, updated = await db.upsert_seen(rows)
    return {"received": len(rows), "inserted": inserted, "updated": updated}


@app.get("/seen", dependencies=[Depends(require_token)])
async def seen_list(
    since: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
) -> dict:
    rows = await db.seen_since(since, limit)
    return {"count": len(rows), "links": rows}


@app.get("/stats", dependencies=[Depends(require_token)])
async def stats() -> dict:
    async with db.pool.acquire() as con:
        by_platform = await con.fetch("SELECT platform, count(*) AS n FROM records GROUP BY platform")
        by_type = await con.fetch("SELECT record_type, count(*) AS n FROM records GROUP BY record_type")
        total = await con.fetchval("SELECT count(*) FROM records")
        matched = await con.fetchval("SELECT count(*) FROM records WHERE match_score > 0")
        seen = await con.fetchval("SELECT count(*) FROM seen_links")
        last = await con.fetchval("SELECT max(captured_at) FROM records")
    return {
        "total": total,
        "matched": matched,
        "seen_links": seen,
        "by_platform": {r["platform"]: r["n"] for r in by_platform},
        "by_type": {r["record_type"]: r["n"] for r in by_type},
        "last_capture": last.isoformat() if last else None,
    }


@app.post("/admin/purge", dependencies=[Depends(require_token)])
async def admin_purge(days: int | None = Query(default=None, ge=0), raw_days: int | None = Query(default=None, ge=0)) -> dict:
    """Run retention now, in order: L0 bodies (raw_days) then L1 records (days). Overrides apply to this call only; 0 = no-op."""
    rd = RAW_BODY_RETENTION_DAYS if raw_days is None else raw_days
    d = RETENTION_DAYS if days is None else days
    n_raw = await db.purge_raw_bodies(rd)
    res = await db.purge_records(d)
    return {"raw_retention_days": rd, "raw_bodies_purged": n_raw, "retention_days": d, **res}


@app.post("/raw", response_model=RawResult, dependencies=[Depends(require_token)])
async def raw_ingest(body: RawBody) -> RawResult:
    """L0 evidence. Hash must equal sha256(body) — the server verifies and rejects mismatches or oversize bodies."""
    import hashlib
    ok, rejected = [], []
    for c in body.captures:
        raw = c.body.encode("utf-8")
        if len(raw) > RAW_MAX_BYTES or hashlib.sha256(raw).hexdigest() != c.payload_hash.lower():
            rejected.append(c.payload_hash)
            continue
        ok.append({**c.model_dump(), "payload_hash": c.payload_hash.lower(), "body_bytes": len(raw), "ext_version": body.version})
    inserted, duplicate = await db.upsert_raw(ok)
    return RawResult(received=len(body.captures), inserted=inserted, duplicate=duplicate, rejected=len(rejected), rejected_hashes=rejected[:50])


@app.get("/provenance", dependencies=[Depends(require_token)])
async def provenance_coverage() -> dict:
    return await db.provenance_coverage()


@app.get("/provenance/{key}", dependencies=[Depends(require_token)])
async def provenance(key: str) -> dict:
    res = await db.provenance(key)
    if res is None:
        raise HTTPException(status_code=404, detail="unknown record key")
    return res


@app.post("/admin/extract", dependencies=[Depends(require_token)])
async def admin_extract(
    since: datetime | None = Query(default=None), limit: int = Query(default=500, ge=1, le=5000), force: bool = Query(default=False)
) -> dict:
    """Run RULE_V1 now over records with no current observation (or changed content). `force=1` re-runs the current
    method version on already-observed records (new observations; old ones are kept — 001C §2)."""
    return await _extract_once("admin", since, limit, force)


@app.get("/observations", dependencies=[Depends(require_token)])
async def observations_list(
    signal_class: str | None = Query(default=None, alias="class"),
    asset_type: str | None = Query(default=None, alias="asset"),
    since: datetime | None = Query(default=None),
    min_conf: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    rows = await extract_store.list_observations(signal_class, asset_type, since, min_conf, limit)
    return {"observations": rows, "count": len(rows)}


@app.get("/observations/{key}", dependencies=[Depends(require_token)])
async def observation_get(key: str, all: bool = Query(default=False)) -> dict:
    res = await extract_store.get_observation(key, all_runs=all)
    if res is None:
        raise HTTPException(status_code=404, detail="no observation for key")
    return res


@app.get("/extract/stats", dependencies=[Depends(require_token)])
async def extract_stats() -> dict:
    return await extract_store.stats()


@app.post("/admin/fx", dependencies=[Depends(require_token)])
async def admin_fx(currency: str = Query(pattern="^(USD|THB)$"), rate_date: date = Query(), lak_per_unit: Decimal = Query(gt=0),
                   source: str = Query(default="MANUAL", pattern="^(BOL_REFERENCE|MANUAL)$")) -> dict:
    """Load one FX reference rate (C2). Idempotent per (currency, date)."""
    await extract_store.upsert_fx(currency, rate_date, lak_per_unit, source)
    return {"currency": currency, "rate_date": rate_date.isoformat(), "lak_per_unit": str(lak_per_unit), "source": source}


@app.post("/mcp", dependencies=[Depends(require_token)])
async def mcp_endpoint(request: Request) -> Response:
    """MCP Streamable HTTP transport (POST only). Read-only tools over LensDB."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=200)
    status, payload = await McpDispatcher(db).handle(body)
    if payload is None:
        return Response(status_code=status)
    return JSONResponse(payload, status_code=status)


@app.get("/mcp")
async def mcp_get() -> Response:
    # No server-initiated stream; clients must POST.
    return Response(status_code=405, headers={"Allow": "POST"})


# Serve the Console at / (same-origin with the API → the browser fetch needs no CORS).
# Mounted last so it doesn't shadow the API routes above.
if pathlib.Path(CONSOLE_DIR).is_dir():
    app.mount("/", StaticFiles(directory=CONSOLE_DIR, html=True), name="console")
