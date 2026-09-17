"""Canonical permalink rules (001E §3) — the server-side superset of the extension's ADR-0002
`normalizeUrl` (src/lib/url.ts). Pure; versioned; fixture-tested on sanitised real variants.

    canonical_permalink(url)  -> canonical URL string (identity for L1-dup)
    post_identity(url)        -> ("facebook", "<post_id>") when the URL names a post unambiguously, else None
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PERMALINK_RULES_VERSION = "1.0.0"

# ADR-0002 set (kept in sync with src/lib/url.ts) + 001E additions.
TRACKING_PARAMS = {
    "fbclid", "mibextid", "__cft__", "__tn__", "__so__", "rdid", "paipv", "notif_t", "notif_id", "ref", "refid", "refsrc",
    "hc_ref", "source", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "_rdr", "eav", "av",
    "comment_tracking", "ft", "lst",
    # 001E §3
    "s", "share_id", "sfnsn", "extid", "d", "vh", "locale", "checkpoint_src", "__xts__", "__eep__", "_ft_", "pagesource",
}
TRACKING_PREFIXES = ("__cft__", "__tn__", "acontext", "__xts__", "utm_")

# Hosts folded to one canonical host per platform.
_FB_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com", "web.facebook.com",
             "touch.facebook.com", "business.facebook.com", "l.facebook.com", "fb.com", "www.fb.com", "fb.watch"}
_TT_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}

_GROUP_POST = re.compile(r"^/groups/([^/]+)/(?:posts|permalink)/(\d+)/?")
_PERMALINK_PHP = re.compile(r"^/permalink\.php$")
_STORY_PHP = re.compile(r"^/story\.php$")
_POSTS = re.compile(r"^/(?:[^/]+)/posts/(?:[^/]+/)?(\d+)/?")           # /<page>/posts/<id>/ or /<page>/posts/pfbid.../<id>
_PFBID_POSTS = re.compile(r"^/(?:[^/]+)/posts/(pfbid[0-9A-Za-z]+)/?")   # opaque ids: canonical but not a numeric post id
_PHOTO = re.compile(r"^/photo(?:\.php)?/?$|^/[^/]+/photos/")
_VIDEO = re.compile(r"^/(?:[^/]+/videos/|watch/?$|reel/)(\d+)?")
_SHARE = re.compile(r"^/share/(?:p|v|r)/([0-9A-Za-z_-]+)/?")
_TT_VIDEO = re.compile(r"^/@[^/]+/video/(\d+)")


def canonical_permalink(url: str) -> str:
    """Identity form of a post URL. Unknown shapes are normalised (host/params/slash) but otherwise kept."""
    raw = (url or "").strip()
    try:
        p = urlsplit(raw)
    except ValueError:
        return raw
    if not p.scheme or not p.netloc:
        return raw
    host = p.netloc.lower().split("@")[-1].split(":")[0]
    if host in _FB_HOSTS:
        host = "www.facebook.com"
    elif host in _TT_HOSTS:
        host = "www.tiktok.com"
    path = re.sub(r"/{2,}", "/", p.path or "/")
    params = _clean_params(p.query)

    if host == "www.facebook.com":
        m = _GROUP_POST.match(path)
        if m:
            return f"https://www.facebook.com/groups/{m.group(1)}/posts/{m.group(2)}/"
        if _PERMALINK_PHP.match(path) and params.get("story_fbid") and params.get("id"):
            return f"https://www.facebook.com/permalink.php?id={params['id']}&story_fbid={params['story_fbid']}"
        if _STORY_PHP.match(path) and params.get("story_fbid") and params.get("id"):
            return f"https://www.facebook.com/permalink.php?id={params['id']}&story_fbid={params['story_fbid']}"
        m = _POSTS.match(path)
        if m:
            page = path.split("/")[1]
            return f"https://www.facebook.com/{page}/posts/{m.group(1)}/"
        m = _PFBID_POSTS.match(path)
        if m:
            page = path.split("/")[1]
            return f"https://www.facebook.com/{page}/posts/{m.group(1)}/"
        if _PHOTO.match(path) and params.get("fbid"):
            return f"https://www.facebook.com/photo/?fbid={params['fbid']}"
        m = _VIDEO.match(path)
        if m and (m.group(1) or params.get("v")):
            return f"https://www.facebook.com/watch/?v={m.group(1) or params['v']}"
        m = _SHARE.match(path)
        if m:
            # Share links are opaque until the payload reveals the post id (001E §3); canonical form kept stable.
            return f"https://www.facebook.com/share/{path.split('/')[2]}/{m.group(1)}/"
    if host == "www.tiktok.com":
        m = _TT_VIDEO.match(path)
        if m:
            return f"https://www.tiktok.com/video/{m.group(1)}"
        m = re.match(r"^/(?:t|v)/([0-9A-Za-z]+)/?", path)
        if m:
            return f"https://www.tiktok.com/t/{m.group(1)}"

    path = path.rstrip("/") + "/" if path != "/" else "/"
    query = urlencode(sorted(params.items()))
    return urlunsplit(("https", host, path, query, ""))


def post_identity(url: str) -> tuple[str, str] | None:
    """(platform, post_id) when the canonical form names a post id; None for opaque/unknown forms."""
    c = canonical_permalink(url)
    p = urlsplit(c)
    if p.netloc == "www.facebook.com":
        m = _GROUP_POST.match(p.path)
        if m:
            return ("facebook", m.group(2))
        if p.path == "/permalink.php":
            q = dict(parse_qsl(p.query))
            return ("facebook", q["story_fbid"]) if "story_fbid" in q else None
        m = _POSTS.match(p.path)
        if m:
            return ("facebook", m.group(1))
        if p.path == "/watch/":
            q = dict(parse_qsl(p.query))
            return ("facebook", q["v"]) if "v" in q else None
        if p.path == "/photo/":
            q = dict(parse_qsl(p.query))
            return ("facebook", q["fbid"]) if "fbid" in q else None
    if p.netloc == "www.tiktok.com":
        m = re.match(r"^/video/(\d+)$", p.path)
        if m:
            return ("tiktok", m.group(1))
    return None


def _clean_params(query: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in parse_qsl(query, keep_blank_values=False):
        kl = k.lower()
        if kl in TRACKING_PARAMS or kl.startswith(TRACKING_PREFIXES):
            continue
        out[k] = v
    return out
