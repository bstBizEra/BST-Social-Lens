"""BST Social Lens — Ingest API (FastAPI).

The single HTTP boundary between the browser extension / Console and LensDB.
Owns no state itself; validate → upsert → record run.

Endpoints
  GET  /health           liveness + record count
  POST /ingest           receive a batch of SocialRecords (bearer auth)
  GET  /records          paginated read for the Console (bearer auth)
  GET  /stats            per-platform counts (bearer auth)
  POST /mcp              Model Context Protocol (Streamable HTTP, read-only tools; bearer auth)
  POST /admin/purge      run the retention purge now (bearer auth); also runs daily in-process
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import Database
from .mcp import McpDispatcher
from .models import HealthResult, IngestBody, IngestResult, SeenBody, record_to_row

DSN = os.environ.get("LENS_DB_DSN", "postgresql://lens:lens@lens-db:5432/lens")
TOKEN = os.environ.get("LENS_API_TOKEN", "")
# Comma-separated origins allowed to call the API from a browser (Console served elsewhere).
CORS_ORIGINS = [o for o in os.environ.get("LENS_CORS_ORIGINS", "").split(",") if o]
# The Social Lens Console static dir; served at / when present (same-origin → no CORS).
CONSOLE_DIR = os.environ.get("LENS_CONSOLE_DIR", "/srv/console")
# Data minimisation: delete records older than this many days (by post date, else capture date).
# 0 disables. Default 730 (24 months). The purge runs at startup and then every 24 h.
RETENTION_DAYS = int(os.environ.get("LENS_RETENTION_DAYS", "730"))
PURGE_INTERVAL_S = 24 * 3600

log = logging.getLogger("lens-api")

db = Database(DSN)


async def _purge_loop() -> None:
    while True:
        try:
            res = await db.purge_records(RETENTION_DAYS)
            if res["records"] or res["seen_links"]:
                log.info("retention purge (%s days): %s", RETENTION_DAYS, res)
        except Exception as e:  # never let the purge take the API down
            log.warning("retention purge failed: %s", e)
        await asyncio.sleep(PURGE_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    task = asyncio.create_task(_purge_loop()) if RETENTION_DAYS > 0 else None
    yield
    if task:
        task.cancel()
    await db.close()


app = FastAPI(title="BST Social Lens — Ingest API", version="0.4.0", lifespan=lifespan)

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
async def admin_purge(days: int | None = Query(default=None, ge=0)) -> dict:
    """Run the retention purge now. `days` overrides LENS_RETENTION_DAYS for this call (0 = no-op)."""
    d = RETENTION_DAYS if days is None else days
    res = await db.purge_records(d)
    return {"retention_days": d, **res}


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
