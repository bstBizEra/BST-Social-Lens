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
    key, platform, post_id, permalink, container_id, container_name,
    author_name, author_id, author_hash, author_url, text, lang,
    created_at, captured_at, reactions_total, reactions_breakdown,
    comments_count, shares_count, views_count, media, hashtags,
    parser_version, ingest_source, ingest_version
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
    $20::jsonb,$21::text[],$22,$23,$24
)
ON CONFLICT (key) DO UPDATE SET
    permalink        = COALESCE(EXCLUDED.permalink, records.permalink),
    container_id     = COALESCE(EXCLUDED.container_id, records.container_id),
    container_name   = COALESCE(EXCLUDED.container_name, records.container_name),
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
    "key", "platform", "post_id", "permalink", "container_id", "container_name",
    "author_name", "author_id", "author_hash", "author_url", "text", "lang",
    "created_at", "captured_at", "reactions_total", "reactions_breakdown",
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
