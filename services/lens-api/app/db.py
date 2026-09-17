"""Async Postgres access via asyncpg. Thin: identity + upsert + a few reads.

The upsert de-dupes on the primary key `key` (`${platform}:${post_id}`).
Engagement counts and last_seen are refreshed on conflict; first_seen and
created_at are preserved.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

import asyncpg

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")
# L2 schemas are separate files, applied after the core schema; schema_lint keeps them additive.
L2_SCHEMA_PATHS = [pathlib.Path(__file__).with_name("schema_extract.sql"), pathlib.Path(__file__).with_name("schema_geo.sql"), pathlib.Path(__file__).with_name("schema_audit.sql")]

_UPSERT = """
INSERT INTO records (
    key, platform, post_id, record_type, parent_post_id, permalink,
    container_id, container_name, container_type, matched_keywords, match_score,
    matched_via, url_hash, author_name, author_id, author_hash, author_url,
    text, lang, created_at, captured_at, reactions_total, reactions_breakdown,
    comments_count, shares_count, views_count, media, hashtags,
    parser_version, ingest_source, ingest_version,
    content_hash, first_payload_hash, last_payload_hash
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::text[],$11,$12,$13,$14,$15,$16,$17,$18,$19,
    $20,$21,$22,$23::jsonb,$24,$25,$26,$27::jsonb,$28::text[],$29,$30,$31,
    $32,$33,$34
)
ON CONFLICT (key) DO UPDATE SET
    record_type      = EXCLUDED.record_type,
    parent_post_id   = COALESCE(EXCLUDED.parent_post_id, records.parent_post_id),
    permalink        = COALESCE(EXCLUDED.permalink, records.permalink),
    container_id     = COALESCE(EXCLUDED.container_id, records.container_id),
    container_name   = COALESCE(EXCLUDED.container_name, records.container_name),
    container_type   = COALESCE(EXCLUDED.container_type, records.container_type),
    matched_keywords = CASE WHEN array_length(EXCLUDED.matched_keywords, 1) > 0 THEN EXCLUDED.matched_keywords ELSE records.matched_keywords END,
    match_score      = GREATEST(EXCLUDED.match_score, records.match_score),
    matched_via      = COALESCE(EXCLUDED.matched_via, records.matched_via),
    url_hash         = COALESCE(EXCLUDED.url_hash, records.url_hash),
    author_name      = COALESCE(EXCLUDED.author_name, records.author_name),
    author_id        = COALESCE(EXCLUDED.author_id, records.author_id),
    author_hash      = COALESCE(EXCLUDED.author_hash, records.author_hash),
    author_url       = COALESCE(EXCLUDED.author_url, records.author_url),
    text             = COALESCE(EXCLUDED.text, records.text),
    lang             = COALESCE(EXCLUDED.lang, records.lang),
    created_at       = COALESCE(records.created_at, EXCLUDED.created_at),
    captured_at      = GREATEST(records.captured_at, EXCLUDED.captured_at),
    reactions_total  = COALESCE(EXCLUDED.reactions_total, records.reactions_total),
    reactions_breakdown = COALESCE(EXCLUDED.reactions_breakdown, records.reactions_breakdown),
    comments_count   = COALESCE(EXCLUDED.comments_count, records.comments_count),
    shares_count     = COALESCE(EXCLUDED.shares_count, records.shares_count),
    views_count      = COALESCE(EXCLUDED.views_count, records.views_count),
    media            = CASE WHEN jsonb_array_length(EXCLUDED.media) > 0 THEN EXCLUDED.media ELSE records.media END,
    hashtags         = CASE WHEN array_length(EXCLUDED.hashtags, 1) > 0 THEN EXCLUDED.hashtags ELSE records.hashtags END,
    parser_version   = COALESCE(EXCLUDED.parser_version, records.parser_version),
    ingest_source    = EXCLUDED.ingest_source,
    ingest_version   = EXCLUDED.ingest_version,
    content_hash     = COALESCE(EXCLUDED.content_hash, records.content_hash),
    first_payload_hash = COALESCE(records.first_payload_hash, EXCLUDED.first_payload_hash),
    last_payload_hash  = COALESCE(EXCLUDED.last_payload_hash, records.last_payload_hash),
    capture_count    = records.capture_count + 1,
    last_seen        = now()
RETURNING (xmax = 0) AS inserted;
"""

# Column order for the upsert parameters.
_COLS = [
    "key", "platform", "post_id", "record_type", "parent_post_id", "permalink",
    "container_id", "container_name", "container_type", "matched_keywords", "match_score",
    "matched_via", "url_hash", "author_name", "author_id", "author_hash", "author_url",
    "text", "lang", "created_at", "captured_at", "reactions_total", "reactions_breakdown",
    "comments_count", "shares_count", "views_count", "media", "hashtags",
    "parser_version", "ingest_source", "ingest_version",
    "content_hash", "first_payload_hash", "last_payload_hash",
]

_CAPTURE_EVENT = """
INSERT INTO capture_events (record_key, payload_hash, captured_at, page_url, parser_version, ingest_source)
VALUES ($1,$2,$3,$4,$5,$6)
ON CONFLICT (record_key, payload_hash, captured_at) DO NOTHING
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self.pgcrypto: bool = False
        self.extract: Any = None  # ExtractStore, attached by main (L2 reads for /mcp)
        self.geo: Any = None  # GeoStore, attached by main

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
        await self.init_db()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("database not connected")
        return self._pool

    async def init_db(self) -> None:
        async with self.pool.acquire() as con:
            await con.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            await con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            # pgcrypto (C4) protects contact raw values; optional — without it raw values are not stored.
            try:
                await con.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
                self.pgcrypto = True
            except Exception:  # noqa: BLE001 — privilege-dependent; degrade, never block startup
                self.pgcrypto = False
            for p in L2_SCHEMA_PATHS:
                await con.execute(p.read_text(encoding="utf-8"))

    async def upsert_records(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        """Returns (inserted, updated)."""
        if not rows:
            return (0, 0)
        inserted = 0
        async with self.pool.acquire() as con:
            async with con.transaction():
                for row in rows:
                    args = []
                    for c in _COLS:
                        v = row.get(c)
                        if c in ("media", "reactions_breakdown") and v is not None:
                            v = json.dumps(v)
                        args.append(v)
                    was_insert = await con.fetchval(_UPSERT, *args)
                    if was_insert:
                        inserted += 1
                    # Provenance: one capture event per sighting (I1), even when the row already existed.
                    await con.execute(
                        _CAPTURE_EVENT, row["key"], row.get("first_payload_hash"), row.get("captured_at_event") or row.get("captured_at"),
                        row.get("page_url"), row.get("parser_version"), row.get("ingest_source"),
                    )
        return (inserted, len(rows) - inserted)

    # ---------- L0 raw captures ----------

    async def upsert_raw(self, captures: list[dict[str, Any]]) -> tuple[int, int]:
        """Insert raw payloads keyed by payload_hash. Returns (inserted, duplicate).
        A duplicate hash never overwrites; the first capture context is the evidence."""
        if not captures:
            return (0, 0)
        inserted = 0
        async with self.pool.acquire() as con:
            async with con.transaction():
                for c in captures:
                    was_insert = await con.fetchval(
                        """
                        INSERT INTO raw_captures (payload_hash, platform, url, method, status, source, page_url,
                                                  captured_at, body, body_bytes, truncated, parser_version, ext_version)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                        ON CONFLICT (payload_hash) DO NOTHING
                        RETURNING true
                        """,
                        c["payload_hash"], c.get("platform"), c["url"], c.get("method"), c.get("status"), c.get("source"),
                        c.get("page_url"), c["captured_at"], c["body"], c.get("body_bytes") or len(c["body"].encode("utf-8")),
                        bool(c.get("truncated")), c.get("parser_version"), c.get("ext_version"),
                    )
                    if was_insert:
                        inserted += 1
        return (inserted, len(captures) - inserted)

    async def provenance(self, key: str) -> dict[str, Any] | None:
        """L1 row → its capture events → raw captures (body presence, not body)."""
        async with self.pool.acquire() as con:
            rec = await con.fetchrow("SELECT key, content_hash, first_payload_hash, last_payload_hash, capture_count, protected, first_seen, last_seen FROM records WHERE key = $1", key)
            if not rec:
                return None
            events = await con.fetch(
                """SELECT e.captured_at, e.payload_hash, e.page_url, e.parser_version, e.ingest_source,
                          (r.payload_hash IS NOT NULL) AS raw_present, (r.body IS NOT NULL) AS body_present, r.body_bytes, r.truncated
                   FROM capture_events e LEFT JOIN raw_captures r ON r.payload_hash = e.payload_hash
                   WHERE e.record_key = $1 ORDER BY e.captured_at""", key)
        return {**dict(rec), "events": [dict(e) for e in events]}

    async def provenance_coverage(self) -> dict[str, Any]:
        """Phase 5 exit metric: share of L1 rows whose first payload is stored as L0."""
        async with self.pool.acquire() as con:
            total = await con.fetchval("SELECT count(*) FROM records")
            with_hash = await con.fetchval("SELECT count(*) FROM records WHERE first_payload_hash IS NOT NULL")
            resolved = await con.fetchval(
                "SELECT count(*) FROM records r WHERE r.first_payload_hash IS NOT NULL AND EXISTS (SELECT 1 FROM raw_captures c WHERE c.payload_hash = r.first_payload_hash)")
            raw_total = await con.fetchval("SELECT count(*) FROM raw_captures")
            raw_with_body = await con.fetchval("SELECT count(*) FROM raw_captures WHERE body IS NOT NULL")
        return {"records": total, "records_with_payload_hash": with_hash, "records_resolved_to_raw": resolved,
                "coverage": round(resolved / total, 4) if total else None,
                "raw_captures": raw_total, "raw_with_body": raw_with_body}

    async def record_ingest_run(
        self, source: str | None, ext_version: str | None, sent: int,
        inserted: int, updated: int, remote_addr: str | None,
    ) -> int:
        async with self.pool.acquire() as con:
            return await con.fetchval(
                """INSERT INTO ingest_runs (source, ext_version, records_sent, inserted, updated, remote_addr)
                   VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
                source, ext_version, sent, inserted, updated, remote_addr,
            )

    async def count_records(self) -> int:
        async with self.pool.acquire() as con:
            return await con.fetchval("SELECT count(*) FROM records")

    async def upsert_seen(self, links: list[dict[str, Any]]) -> tuple[int, int]:
        """Upsert seen-link frontier rows. Returns (inserted, updated)."""
        if not links:
            return (0, 0)
        inserted = 0
        async with self.pool.acquire() as con:
            async with con.transaction():
                for row in links:
                    was_insert = await con.fetchval(
                        """
                        INSERT INTO seen_links (url_hash, url, platform, last_status, fetch_count, refresh_after)
                        VALUES ($1,$2,$3,$4,$5,$6)
                        ON CONFLICT (url_hash) DO UPDATE SET
                            last_status   = EXCLUDED.last_status,
                            fetch_count   = GREATEST(seen_links.fetch_count, EXCLUDED.fetch_count),
                            refresh_after = COALESCE(EXCLUDED.refresh_after, seen_links.refresh_after),
                            updated_at    = now()
                        RETURNING (xmax = 0) AS inserted
                        """,
                        row["url_hash"], row["url"], row.get("platform"),
                        row.get("last_status", "seen"), row.get("fetch_count", 0),
                        row.get("refresh_after"),
                    )
                    if was_insert:
                        inserted += 1
        return (inserted, len(links) - inserted)

    async def seen_since(self, since: str | None, limit: int) -> list[dict[str, Any]]:
        async with self.pool.acquire() as con:
            if since:
                rows = await con.fetch(
                    "SELECT * FROM seen_links WHERE updated_at > $1 ORDER BY updated_at LIMIT $2", since, limit
                )
            else:
                rows = await con.fetch("SELECT * FROM seen_links ORDER BY updated_at LIMIT $1", limit)
        return [dict(r) for r in rows]

    # ---------- read helpers used by the MCP adapter (read-only) ----------

    async def search_records(
        self, *, query: str | None, platform: str | None, record_type: str | None, container_id: str | None,
        keyword: str | None, matched_only: bool, since: Any | None, limit: int,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if query:
            args.append(f"%{query}%"); clauses.append(f"text ILIKE ${len(args)}")
        if platform:
            args.append(platform); clauses.append(f"platform = ${len(args)}")
        if record_type:
            args.append(record_type); clauses.append(f"record_type = ${len(args)}")
        if container_id:
            args.append(container_id); clauses.append(f"container_id = ${len(args)}")
        if keyword:
            args.append(keyword); clauses.append(f"${len(args)} = ANY(matched_keywords)")
        if matched_only:
            clauses.append("match_score > 0")
        if since:
            args.append(since); clauses.append(f"COALESCE(created_at, captured_at) > ${len(args)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        sql = (f"SELECT key, platform, post_id, record_type, parent_post_id, permalink, container_id, container_name, "
               f"container_type, author_name, author_hash, text, lang, created_at, captured_at, reactions_total, "
               f"comments_count, shares_count, views_count, hashtags, matched_keywords, match_score "
               f"FROM records {where} ORDER BY COALESCE(created_at, captured_at) DESC NULLS LAST LIMIT ${len(args)}")
        async with self.pool.acquire() as con:
            rows = await con.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def get_record_with_comments(self, key: str) -> dict[str, Any] | None:
        async with self.pool.acquire() as con:
            rec = await con.fetchrow("SELECT * FROM records WHERE key = $1", key)
            if not rec:
                return None
            post_id = rec["post_id"]
            comments = await con.fetch(
                "SELECT key, author_name, author_hash, text, created_at, reactions_total, matched_keywords "
                "FROM records WHERE record_type = 'comment' AND parent_post_id = $1 ORDER BY created_at NULLS LAST LIMIT 200",
                post_id,
            )
        out = dict(rec)
        out["comments"] = [dict(c) for c in comments]
        return out

    async def stats(self) -> dict[str, Any]:
        async with self.pool.acquire() as con:
            by_platform = await con.fetch("SELECT platform, count(*) AS n FROM records GROUP BY platform")
            by_type = await con.fetch("SELECT record_type, count(*) AS n FROM records GROUP BY record_type")
            total = await con.fetchval("SELECT count(*) FROM records")
            matched = await con.fetchval("SELECT count(*) FROM records WHERE match_score > 0")
            seen = await con.fetchval("SELECT count(*) FROM seen_links")
            last = await con.fetchval("SELECT max(captured_at) FROM records")
        return {
            "total": total, "matched": matched, "seen_links": seen,
            "by_platform": {r["platform"]: r["n"] for r in by_platform},
            "by_type": {r["record_type"]: r["n"] for r in by_type},
            "last_capture": last.isoformat() if last else None,
        }

    async def top_containers(self, *, platform: str | None, limit: int) -> list[dict[str, Any]]:
        args: list[Any] = []
        where = "WHERE match_score > 0"
        if platform:
            args.append(platform); where += f" AND platform = ${len(args)}"
        args.append(limit)
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                f"SELECT platform, container_id, max(container_name) AS container_name, count(*) AS matched, "
                f"max(COALESCE(created_at, captured_at)) AS latest FROM records {where} "
                f"GROUP BY platform, container_id ORDER BY matched DESC LIMIT ${len(args)}", *args)
        return [dict(r) for r in rows]

    async def seen_list(self, *, status: str | None, limit: int) -> list[dict[str, Any]]:
        async with self.pool.acquire() as con:
            if status:
                rows = await con.fetch("SELECT * FROM seen_links WHERE last_status = $1 ORDER BY updated_at DESC LIMIT $2", status, limit)
            else:
                rows = await con.fetch("SELECT * FROM seen_links ORDER BY updated_at DESC LIMIT $1", limit)
        return [dict(r) for r in rows]

    # ---------- retention ----------

    async def purge_raw_bodies(self, body_retention_days: int) -> int:
        """L0 body retention: NULL the body (keep hash + context) after N days. 0 disables."""
        if body_retention_days <= 0:
            return 0
        async with self.pool.acquire() as con:
            n = await con.fetchval(
                "WITH u AS (UPDATE raw_captures SET body = NULL, body_purged_at = now() "
                "WHERE body IS NOT NULL AND captured_at < now() - ($1::int * interval '1 day') RETURNING 1) SELECT count(*) FROM u",
                body_retention_days)
        return int(n or 0)

    async def purge_records(self, retention_days: int) -> dict[str, int]:
        """L1 retention: delete records (and their comments) older than N days by post date
        (else capture date), except `protected` rows (referenced by a published dataset, D3),
        plus frontier rows no record references. Capture events of deleted records are kept
        (they reference only hashes). 0 disables."""
        if retention_days <= 0:
            return {"records": 0, "seen_links": 0}
        async with self.pool.acquire() as con:
            async with con.transaction():
                n_rec = await con.fetchval(
                    "WITH d AS (DELETE FROM records WHERE NOT protected AND COALESCE(created_at, captured_at) < now() - ($1::int * interval '1 day') RETURNING 1) "
                    "SELECT count(*) FROM d", retention_days)
                n_seen = await con.fetchval(
                    "WITH d AS (DELETE FROM seen_links s WHERE s.updated_at < now() - ($1::int * interval '1 day') "
                    "AND NOT EXISTS (SELECT 1 FROM records r WHERE r.url_hash = s.url_hash) RETURNING 1) SELECT count(*) FROM d",
                    retention_days)
        return {"records": int(n_rec or 0), "seen_links": int(n_seen or 0)}
