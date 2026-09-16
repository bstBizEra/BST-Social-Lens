"""Extraction from a LOGGED-OUT public page — pure, fixture-testable.

We deliberately extract only what any anonymous visitor sees: Open Graph
metadata, the canonical URL, and engagement counters embedded in the page's
own JSON. No member data, no profile aggregation.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["ok", "login-wall", "blocked", "not-found", "empty"]


@dataclass
class Extracted:
    outcome: Outcome
    post_id: str | None = None
    platform: str | None = None
    permalink: str | None = None
    text: str | None = None
    author_name: str | None = None
    author_url: str | None = None
    created_at: str | None = None
    reactions_total: int | None = None
    comments_count: int | None = None
    shares_count: int | None = None
    views_count: int | None = None
    media: list[dict[str, str]] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    lang: str | None = None
    signals: list[str] = field(default_factory=list)


_META = re.compile(r'<meta\s+(?:property|name)=["\']([^"\']+)["\']\s+content=["\']([^"\']*)["\']', re.I)
_META_REV = re.compile(r'<meta\s+content=["\']([^"\']*)["\']\s+(?:property|name)=["\']([^"\']+)["\']', re.I)
# Lao/Thai have combining marks that \w misses; take any run up to whitespace/#.
_HASHTAG = re.compile(r"#([^\s#]+)")

LOGIN_WALL_SIGNALS = (
    "login_form", "/login/?next=", "You must log in to continue", "log in or sign up",
    'id="login_form"', "Log in to Facebook", "checkpoint/", "ເຂົ້າສູ່ລະບົບ",
)
BLOCK_SIGNALS = (
    "Access Denied", "captcha", "verify you are human", "Please wait while we verify",
    "cf-chl", "challenge-platform", "rate limit", "Too Many Requests", "tiktok-verify-page",
)


def _metas(page: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in _META.findall(page):
        out.setdefault(k.lower(), html.unescape(v))
    for v, k in _META_REV.findall(page):
        out.setdefault(k.lower(), html.unescape(v))
    return out


def _int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def classify_response(status: int, page: str) -> Outcome:
    if status in (403, 429):
        return "blocked"
    if status == 404 or status == 410:
        return "not-found"
    low = page[:200_000].lower()
    if any(s.lower() in low for s in BLOCK_SIGNALS):
        return "blocked"
    if any(s.lower() in low for s in LOGIN_WALL_SIGNALS):
        return "login-wall"
    if not page.strip():
        return "empty"
    return "ok"


# ---------- TikTok ----------

def _tiktok_rehydration(page: str) -> dict[str, Any] | None:
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def extract_tiktok(status: int, page: str, url: str) -> Extracted:
    out = Extracted(outcome=classify_response(status, page), platform="tiktok", permalink=url)
    if out.outcome != "ok":
        return out
    data = _tiktok_rehydration(page)
    item: dict[str, Any] | None = None
    if data:
        scope = data.get("__DEFAULT_SCOPE__", {})
        detail = scope.get("webapp.video-detail", {})
        if detail.get("statusCode", 0) not in (0, None):
            out.outcome = "login-wall" if detail.get("statusCode") in (10204, 10216) else "not-found"
            out.signals.append(f"video-detail statusCode={detail.get('statusCode')}")
            return out
        item = (detail.get("itemInfo") or {}).get("itemStruct")
    if item:
        out.signals.append("rehydration-json")
        out.post_id = str(item.get("id") or "")
        out.text = item.get("desc")
        out.created_at = _iso(item.get("createTime"))
        author = item.get("author") or {}
        out.author_name = author.get("nickname") or author.get("uniqueId")
        if author.get("uniqueId"):
            out.author_url = f"https://www.tiktok.com/@{author['uniqueId']}"
        stats = item.get("stats") or item.get("statsV2") or {}
        out.reactions_total = _int(stats.get("diggCount"))
        out.comments_count = _int(stats.get("commentCount"))
        out.shares_count = _int(stats.get("shareCount"))
        out.views_count = _int(stats.get("playCount"))
        cover = (item.get("video") or {}).get("cover")
        if cover:
            out.media.append({"kind": "video", "url": url, "thumbnail": cover})
        out.hashtags = [c.get("hashtagName") for c in item.get("textExtra") or [] if c.get("hashtagName")]
        out.lang = item.get("textLanguage")
    else:
        metas = _metas(page)
        out.signals.append("og-only")
        m = re.search(r"/video/(\d+)", url)
        out.post_id = m.group(1) if m else None
        out.text = metas.get("og:description") or metas.get("description")
        if metas.get("og:image"):
            out.media.append({"kind": "video", "url": url, "thumbnail": metas["og:image"]})
    if out.text and not out.hashtags:
        out.hashtags = [h.rstrip(".,!?") for h in _HASHTAG.findall(out.text)]
    if not out.post_id:
        out.outcome = "empty"
    return out


# ---------- Facebook ----------

_FB_COUNT_PATTERNS = {
    "reactions_total": [r'"reaction_count":\{"count":(\d+)', r'"i18n_reaction_count":"([\d,\.]+)"'],
    "comments_count": [r'"comment_rendering_instance":\{"comments":\{"total_count":(\d+)', r'"comment_count":\{"total_count":(\d+)', r'"total_comment_count":(\d+)'],
    "shares_count": [r'"share_count":\{"count":(\d+)'],
}


def extract_facebook(status: int, page: str, url: str) -> Extracted:
    out = Extracted(outcome=classify_response(status, page), platform="facebook", permalink=url)
    if out.outcome != "ok":
        return out
    metas = _metas(page)
    out.signals.append("og")
    out.text = metas.get("og:description") or metas.get("description")
    title = metas.get("og:title")
    if title and " | " not in title and title.lower() != "facebook":
        out.author_name = title.split(" - ")[0].strip() or None
    if metas.get("og:image"):
        out.media.append({"kind": "image", "url": metas["og:image"]})
    m = re.search(r"/(?:posts|permalink)/(\d+)", url) or re.search(r"story_fbid=(\d+)", url) or re.search(r"[?&]v=(\d+)", url)
    out.post_id = m.group(1) if m else None
    for field_name, pats in _FB_COUNT_PATTERNS.items():
        for pat in pats:
            mm = re.search(pat, page)
            if mm:
                setattr(out, field_name, _int(mm.group(1).replace(",", "").replace(".", "")))
                out.signals.append(f"embedded:{field_name}")
                break
    mm = re.search(r'"publish_time":(\d{9,11})', page) or re.search(r'"creation_time":(\d{9,11})', page)
    if mm:
        out.created_at = _iso(int(mm.group(1)))
    if out.text:
        out.hashtags = [h.rstrip(".,!?") for h in _HASHTAG.findall(out.text)]
    if not out.post_id or (not out.text and not out.media and out.reactions_total is None):
        out.outcome = "empty"
    return out


def _iso(epoch: Any) -> str | None:
    try:
        from datetime import datetime, timezone
        e = int(epoch)
        if e > 10**12:
            e //= 1000
        return datetime.fromtimestamp(e, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def extract(platform: str, status: int, page: str, url: str) -> Extracted:
    if platform == "tiktok":
        return extract_tiktok(status, page, url)
    if platform == "facebook":
        return extract_facebook(status, page, url)
    return Extracted(outcome="empty", platform=platform, permalink=url)


def to_record(x: Extracted, captured_at: str, parser_version: str) -> dict[str, Any] | None:
    """Shape an Extracted into a SocialRecord v1 dict for POST /ingest.
    Author ids are never present in logged-out HTML we parse, so nothing to hash."""
    if x.outcome != "ok" or not x.post_id or not x.platform:
        return None
    return {
        "key": f"{x.platform}:{x.post_id}",
        "platform": x.platform,
        "post_id": x.post_id,
        "record_type": "post",
        "permalink": x.permalink,
        "author_name": x.author_name,
        "author_url": x.author_url,
        "text": x.text,
        "lang": x.lang,
        "created_at": x.created_at,
        "captured_at": captured_at,
        "reactions_total": x.reactions_total,
        "comments_count": x.comments_count,
        "shares_count": x.shares_count,
        "views_count": x.views_count,
        "media": x.media,
        "hashtags": x.hashtags,
        "parser_version": parser_version,
        "matched_keywords": [],
        "match_score": 0,
    }
