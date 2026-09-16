"""Layer C spike runner.

    python -m worker.run --api http://localhost:7710 --token $LENS_API_TOKEN --limit 50
    python -m worker.run --urls urls.txt --dry-run            # no API; just fetch + report

Pulls up to N frontier links with last_status='seen', fetches each LOGGED OUT
(curl_cffi → nodriver fallback), extracts public fields, pushes records to
/ingest and statuses to /seen (unless --dry-run), and writes a JSON report with
the block rate and a GO / NO-GO decision (ADR-0004 rule 5).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import PARSER_VERSION, WORKER_VERSION
from .client import LensApi
from .extract import classify_response, extract, to_record
from .fetch import fetch_with_fallback, jitter
from .frontier import Target, eligibility_of, platform_of, select_targets
from .report import Attempt, seen_status_for, summarize

FIELDS = ("text", "author_name", "created_at", "reactions_total", "comments_count", "shares_count", "views_count", "media", "hashtags")


def _needs_render(status: int, body: str) -> bool:
    """Escalate to the browser only when HTTP gave a shell with no usable data
    and it is NOT a login wall / block (those are final)."""
    oc = classify_response(status, body)
    if oc in ("login-wall", "blocked", "not-found"):
        return False
    if oc == "empty":
        return True
    # OK status but no rehydration JSON / OG description → likely needs JS.
    return ('__UNIVERSAL_DATA_FOR_REHYDRATION__' not in body) and ('og:description' not in body)


async def run(args: argparse.Namespace) -> int:
    api = LensApi(args.api, args.token) if args.api else None
    targets: list[Target]
    if args.urls:
        urls = [l.strip() for l in Path(args.urls).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        targets = [Target(url_hash="", url=u, platform=platform_of(u), eligibility=eligibility_of(u)) for u in urls][: args.limit]
    else:
        if not api:
            print("error: --api or --urls required", file=sys.stderr)
            return 2
        h = api.health()
        print(f"lens-api: {h.get('status')} db={h.get('db')} records={h.get('records')}")
        targets = select_targets(api.seen(limit=5000), args.limit, include_login_likely=not args.skip_login_likely)
    if not targets:
        print("no eligible frontier targets (need last_status='seen')")
        return 1
    print(f"worker {WORKER_VERSION} — {len(targets)} targets, logged-out, browser fallback={'on' if args.browser else 'off'}")

    attempts: list[Attempt] = []
    records: list[dict] = []
    marks: list[dict] = []
    for i, t in enumerate(targets, 1):
        started = time.time()
        r = await fetch_with_fallback(t.url, use_browser_fallback=args.browser, needs_render=_needs_render)
        if r.error and not r.body:
            x_outcome, fields, signals = "error", [], []
            rec = None
        else:
            x = extract(t.platform, r.status, r.body, t.url)
            x_outcome, signals = x.outcome, x.signals
            rec = to_record(x, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), PARSER_VERSION)
            fields = [f for f in FIELDS if rec and rec.get(f) not in (None, [], "")]
        seen_status = seen_status_for(x_outcome)
        attempts.append(Attempt(url=t.url, platform=t.platform, eligibility=t.eligibility, fetcher=r.fetcher, status=r.status,
                                outcome=x_outcome, seen_status=seen_status, fields_filled=fields, error=r.error, signals=signals))
        if rec:
            records.append(rec)
        if t.url_hash:
            marks.append({"url_hash": t.url_hash, "url": t.url, "platform": t.platform, "last_status": seen_status,
                          "fetch_count": 1 if seen_status == "fetched" else 0})
        print(f"[{i:>3}/{len(targets)}] {x_outcome:<10} {r.fetcher:<9} {r.status:<3} {len(fields)} fields  {t.url[:80]}  ({time.time()-started:.1f}s)")
        if i < len(targets):
            await asyncio.sleep(jitter(args.min_delay, args.max_delay))

    summary = summarize(attempts)
    if api and not args.dry_run:
        if records:
            res = api.ingest(records, source="bst-lens-worker", version=WORKER_VERSION)
            print(f"ingest: received={res.get('received')} inserted={res.get('inserted')} updated={res.get('updated')}")
        if marks:
            res = api.mark_seen(marks)
            print(f"seen: updated={res.get('updated')} inserted={res.get('inserted')}")
    out = Path(args.out)
    out.write_text(json.dumps({"worker": WORKER_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), **summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "attempts_detail"}, ensure_ascii=False, indent=2))
    print(f"report → {out}   decision: {summary['decision']}")
    return 0 if summary["decision"] == "GO" else 3


def main() -> None:
    p = argparse.ArgumentParser(description="BST Social Lens — Layer C logged-out spike")
    p.add_argument("--api", default=os.environ.get("LENS_API_URL", ""), help="lens-api base URL (e.g. http://localhost:7710)")
    p.add_argument("--token", default=os.environ.get("LENS_API_TOKEN", ""))
    p.add_argument("--urls", help="text file of permalinks (bypasses the API frontier)")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--browser", action="store_true", help="enable nodriver fallback (needs Chrome/Chromium)")
    p.add_argument("--skip-login-likely", action="store_true", help="skip Facebook group permalinks")
    p.add_argument("--dry-run", action="store_true", help="fetch + report only; no /ingest or /seen writes")
    p.add_argument("--min-delay", type=float, default=4.0)
    p.add_argument("--max-delay", type=float, default=9.0)
    p.add_argument("--out", default="layer-c-report.json")
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
