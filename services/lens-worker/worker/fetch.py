"""Logged-out fetchers.

Primary: curl_cffi impersonating Chrome's TLS/HTTP2 fingerprint — cheap, no
browser. Fallback: nodriver (raw CDP, no Playwright handshake) with a fresh,
cookie-less profile for pages that only render client-side.

Invariant (ADR-0004 rule 3): NO cookies, NO credentials, NO session reuse
across targets beyond a plain HTTP keep-alive. If a page needs login it is
reported as `login-wall`, never retried with a session.
"""
from __future__ import annotations

import asyncio
import random
import tempfile
from dataclasses import dataclass

DEFAULT_UA_IMPERSONATE = "chrome"  # curl_cffi picks the latest Chrome profile it ships


@dataclass
class FetchResult:
    status: int
    body: str
    fetcher: str  # "curl_cffi" | "nodriver"
    final_url: str
    error: str | None = None


def jitter(lo: float, hi: float) -> float:
    return random.uniform(min(lo, hi), max(lo, hi))


def fetch_http(url: str, timeout: float = 25.0, lang: str = "lo-LA,lo;q=0.9,th;q=0.8,en;q=0.7") -> FetchResult:
    from curl_cffi import requests as creq

    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": lang,
        "upgrade-insecure-requests": "1",
    }
    try:
        # A fresh Session per call → no cookie carry-over between targets.
        with creq.Session(impersonate=DEFAULT_UA_IMPERSONATE) as s:
            r = s.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            return FetchResult(status=r.status_code, body=r.text, fetcher="curl_cffi", final_url=str(r.url))
    except Exception as e:  # network / TLS / timeout — report, never raise into the run loop
        return FetchResult(status=0, body="", fetcher="curl_cffi", final_url=url, error=f"{type(e).__name__}: {e}")


async def fetch_browser(url: str, timeout: float = 30.0, headless: bool = True) -> FetchResult:
    """nodriver fallback with a throwaway profile (no cookies, no login)."""
    try:
        import nodriver as uc
    except ImportError as e:  # pragma: no cover
        return FetchResult(status=0, body="", fetcher="nodriver", final_url=url, error=f"nodriver missing: {e}")

    profile = tempfile.mkdtemp(prefix="lens-worker-")
    browser = None
    try:
        browser = await uc.start(headless=headless, user_data_dir=profile, browser_args=["--lang=lo-LA", "--disable-notifications"])
        tab = await browser.get(url)
        # Let client-side rendering settle; TikTok injects rehydration JSON early, FB streams.
        await tab.sleep(jitter(2.5, 4.5))
        body = await tab.get_content()
        final_url = tab.target.url if tab.target else url
        return FetchResult(status=200 if body else 0, body=body or "", fetcher="nodriver", final_url=final_url)
    except Exception as e:
        return FetchResult(status=0, body="", fetcher="nodriver", final_url=url, error=f"{type(e).__name__}: {e}")
    finally:
        try:
            if browser:
                browser.stop()
        except Exception:
            pass


async def fetch_with_fallback(url: str, *, use_browser_fallback: bool, needs_render: "callable") -> FetchResult:
    """curl_cffi first; escalate to nodriver only when the HTTP body is not usable
    AND the outcome is not a login wall (a login wall is final — rule 3)."""
    r = await asyncio.to_thread(fetch_http, url)
    if not use_browser_fallback:
        return r
    if r.error or needs_render(r.status, r.body):
        rb = await fetch_browser(url)
        if not rb.error and rb.body:
            return rb
    return r
