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

_UPSERT = """
INSERT INTO records (
    key, platform, post_id, record_type, parent_post_id, permalink,
    container_id, container_name, container_type, matched_keywords, match_score,
    matched_via, url_hash, author_name, author_id, author_hash, author_url,
    text, lang, created_at, captured_at, reactions_total, reactions_breakdown,
    comments_count, shares_count, views_count, media, hashtags,
    parser_version, ingest_source, ingest_version
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::text[],$11,$12,$13,$14,$15,$16,$17,$18,$19,
    $20,$21,$22,$23::jsonb,$24,$25,$26,$27::jsonb,$28::text[],$29,$30,$31
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
]


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

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
        return (inserted, len(rows) - inserted)

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

    async def purge_records(self, retention_days: int) -> dict[str, int]:
        """Delete records (and their comments) whose post date — created_at, else captured_at —
        is older than `retention_days`, plus frontier rows no record references any more.
        Returns counts. A retention of 0 disables purging (returns zeros)."""
        if retention_days <= 0:
            return {"records": 0, "seen_links": 0}
        async with self.pool.acquire() as con:
            async with con.transaction():
                n_rec = await con.fetchval(
                    "WITH d AS (DELETE FROM records WHERE COALESCE(created_at, captured_at) < now() - ($1::int * interval '1 day') RETURNING 1) "
                    "SELECT count(*) FROM d", retention_days)
                n_seen = await con.fetchval(
                    "WITH d AS (DELETE FROM seen_links s WHERE s.updated_at < now() - ($1::int * interval '1 day') "
                    "AND NOT EXISTS (SELECT 1 FROM records r WHERE r.url_hash = s.url_hash) RETURNING 1) SELECT count(*) FROM d",
                    retention_days)
        return {"records": int(n_rec or 0), "seen_links": int(n_seen or 0)}
