"""Frontier selection — which seen links are eligible for a logged-out fetch.

Pure functions; the API client is in `client.py`.
"""
from __future__ import annotations

import ipaddress
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


ALLOWED_HOST_SUFFIXES = ("facebook.com", "tiktok.com")


class TargetRejected(ValueError):
    """The URL failed the acquisition boundary (ADR-0004 Layer C scope)."""


def validate_target(url: str) -> str:
    """Acquisition boundary — every URL passes this before any network call,
    whether it came from the frontier API or an experiment file.

        HTTPS only → Facebook/TikTok host allow-list → no IP literal / localhost /
        private / link-local → recognised permalink pattern (eligibility != unsupported)

    Returns the URL unchanged on success; raises TargetRejected otherwise.
    """
    try:
        p = urlparse(url.strip())
    except ValueError as e:  # pragma: no cover
        raise TargetRejected(f"unparseable: {e}") from e
    if p.scheme != "https":
        raise TargetRejected("scheme must be https")
    host = (p.hostname or "").lower().rstrip(".")
    if not host or p.username or p.password:
        raise TargetRejected("missing host or credentials in URL")
    if p.port not in (None, 443):
        raise TargetRejected("non-standard port")
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        raise TargetRejected("localhost")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        raise TargetRejected("IP literal not allowed")
    if not any(host == suf or host.endswith("." + suf) for suf in ALLOWED_HOST_SUFFIXES):
        raise TargetRejected(f"host not in allow-list: {host}")
    if eligibility_of(url) == "unsupported":
        raise TargetRejected("not a recognised public permalink pattern")
    return url


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
        try:
            validate_target(url)
        except TargetRejected:
            continue
        elig = eligibility_of(url)
        if elig == "login-likely" and not include_login_likely:
            continue
        out.append(Target(url_hash=l["url_hash"], url=url, platform=platform_of(url), eligibility=elig))
        if len(out) >= limit:
            break
    return out


def targets_from_experiment_file(lines: list[str], limit: int) -> tuple[list[Target], list[tuple[str, str]]]:
    """OFFLINE EXPERIMENT INPUT ONLY — not an acquisition path. Same boundary as the
    frontier; returns (accepted targets, rejected (url, reason)) so the report can show
    what was refused."""
    accepted: list[Target] = []
    rejected: list[tuple[str, str]] = []
    for raw in lines:
        u = raw.strip()
        if not u or u.startswith("#"):
            continue
        try:
            validate_target(u)
        except TargetRejected as e:
            rejected.append((u, str(e)))
            continue
        accepted.append(Target(url_hash="", url=u, platform=platform_of(u), eligibility=eligibility_of(u)))
        if len(accepted) >= limit:
            break
    return accepted, rejected
