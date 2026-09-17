"""Pydantic models — mirror the extension's SocialRecord v1 schema.

Validation is deliberately lenient: the extension is the source of the shape,
and a stricter server would reject records on harmless drift. Unknown fields
are ignored; required fields are only those needed for identity and storage.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["facebook", "tiktok"]


class Media(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str
    url: str
    thumbnail: str | None = None


class SocialRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    platform: Platform
    post_id: str
    record_type: str = "post"
    parent_post_id: str | None = None
    permalink: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    container_type: str | None = None
    matched_keywords: list[str] = Field(default_factory=list)
    match_score: int = 0
    matched_via: str | None = None
    url_hash: str | None = None
    author_name: str | None = None
    author_id: str | None = None
    author_hash: str | None = None
    author_url: str | None = None
    text: str | None = None
    lang: str | None = None
    created_at: datetime | None = None
    captured_at: datetime
    reactions_total: int | None = None
    reactions_breakdown: dict[str, int] | None = None
    comments_count: int | None = None
    shares_count: int | None = None
    views_count: int | None = None
    media: list[Media] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    parser_version: str | None = None
    # Phase 5 provenance (optional; older extensions omit them)
    payload_hash: str | None = None
    content_hash: str | None = None


class RawCapture(BaseModel):
    """L0 payload as captured by the extension. `body` is the (possibly truncated) text."""
    model_config = ConfigDict(extra="ignore")
    payload_hash: str = Field(min_length=64, max_length=64)
    platform: str | None = None
    url: str
    method: str | None = None
    status: int | None = None
    source: str | None = None
    page_url: str | None = None
    captured_at: datetime
    body: str
    body_bytes: int | None = None
    truncated: bool = False
    parser_version: str | None = None


class RawBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str | None = None
    version: str | None = None
    captures: list[RawCapture] = Field(default_factory=list)


class RawResult(BaseModel):
    received: int
    inserted: int
    duplicate: int
    rejected: int
    rejected_hashes: list[str] = Field(default_factory=list)


class IngestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str | None = None
    version: str | None = None
    records: list[SocialRecord] = Field(default_factory=list)


class SeenLink(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url_hash: str
    url: str
    platform: str | None = None
    first_seen: datetime | None = None
    last_status: str = "seen"
    fetch_count: int = 0
    refresh_after: datetime | None = None


class SeenBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    links: list[SeenLink] = Field(default_factory=list)


class IngestResult(BaseModel):
    received: int
    inserted: int
    updated: int
    run_id: int | None = None


class HealthResult(BaseModel):
    status: str
    db: bool
    records: int | None = None


def record_to_row(rec: SocialRecord, source: str | None, version: str | None) -> dict[str, Any]:
    """Flatten a validated record into the column dict used by the upsert.

    Pure and DB-free so it can be unit-tested without Postgres.
    """
    return {
        "key": rec.key,
        "platform": rec.platform,
        "post_id": rec.post_id,
        "record_type": rec.record_type,
        "parent_post_id": rec.parent_post_id,
        "permalink": rec.permalink,
        "container_id": rec.container_id,
        "container_name": rec.container_name,
        "container_type": rec.container_type,
        "matched_keywords": rec.matched_keywords,
        "match_score": rec.match_score,
        "matched_via": rec.matched_via,
        "url_hash": rec.url_hash,
        "author_name": rec.author_name,
        "author_id": rec.author_id,
        "author_hash": rec.author_hash,
        "author_url": rec.author_url,
        "text": rec.text,
        "lang": rec.lang,
        "created_at": rec.created_at,
        "captured_at": rec.captured_at,
        "reactions_total": rec.reactions_total,
        "reactions_breakdown": rec.reactions_breakdown,
        "comments_count": rec.comments_count,
        "shares_count": rec.shares_count,
        "views_count": rec.views_count,
        "media": [m.model_dump(exclude_none=True) for m in rec.media],
        "hashtags": rec.hashtags,
        "parser_version": rec.parser_version,
        "content_hash": rec.content_hash,
        "first_payload_hash": rec.payload_hash,
        "last_payload_hash": rec.payload_hash,
        "captured_at_event": rec.captured_at,
        "page_url": None,
        "ingest_source": source,
        "ingest_version": version,
    }
