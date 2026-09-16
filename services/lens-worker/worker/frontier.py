"""Frontier selection — which seen links are eligible for a logged-out fetch.

Pure functions; the API client is in `client.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

Platform = Literal["facebook", "tiktok", "unknown"]
Eligibility = Literal["public", "login-likely", "unsupported"]


@dataclass(frozen=True)
class Target:
    url_hash: str
    url: str
    platform: Platform
    eligibility: Eligibility


_FB_GROUP_POST = re.compile(r"^/groups/[^/]+/(?:posts|permalink)/\d+/?$")
_FB_PAGE_POST = re.compile(r"^/[^/]+/posts/[\w.-]+/?$")
_FB_STORY = re.compile(r"^/story\.php$")
_FB_PHOTO_VIDEO = re.compile(r"^/(?:photo|video|watch|reel)(?:/|\.php|$)")
_TT_VIDEO = re.compile(r"^/@[^/]+/video/\d+/?$")
_TT_PHOTO = re.compile(r"^/@[^/]+/photo/\d+/?$")


def platform_of(url: str) -> Platform:
    host = urlparse(url).hostname or ""
    if host.endswith("facebook.com"):
        return "facebook"
    if host.endswith("tiktok.com"):
        return "tiktok"
    return "unknown"


def eligibility_of(url: str) -> Eligibility:
    """Classify without touching the network.

    Facebook group posts are usually behind a login wall for private groups;
    we still try them once (public groups exist) but flag them so the report
    separates 'blocked because private' from 'blocked because bot-detected'.
    """
    p = urlparse(url)
    path = p.path or "/"
    plat = platform_of(url)
    if plat == "tiktok":
        return "public" if (_TT_VIDEO.match(path) or _TT_PHOTO.match(path)) else "unsupported"
    if plat == "facebook":
        if _FB_GROUP_POST.match(path):
            return "login-likely"
        if _FB_PAGE_POST.match(path) or _FB_STORY.match(path) or _FB_PHOTO_VIDEO.match(path):
            return "public"
        return "unsupported"
    return "unsupported"


def select_targets(links: list[dict], limit: int, *, include_login_likely: bool = True) -> list[Target]:
    """Pick up to `limit` frontier rows with last_status == 'seen' (never re-fetch
    what the extension or a previous run already handled)."""
    out: list[Target] = []
    for l in links:
        if l.get("last_status", "seen") != "seen":
            continue
        url = l.get("url") or ""
        elig = eligibility_of(url)
        if elig == "unsupported":
            continue
        if elig == "login-likely" and not include_login_likely:
            continue
        out.append(Target(url_hash=l["url_hash"], url=url, platform=platform_of(url), eligibility=elig))
        if len(out) >= limit:
            break
    return out
