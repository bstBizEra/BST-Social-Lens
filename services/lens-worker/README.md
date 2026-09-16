# lens-worker — Layer C spike (logged-out, public permalinks)

Status: **spike** (ADR-0004 rule 5 — graduates to a service only on a GO decision).

## What it does

1. Pulls frontier rows with `last_status='seen'` from lens-api `GET /seen` (or a URL list).
2. Fetches each permalink **logged out** — fresh `curl_cffi` session impersonating Chrome
   (no cookies), optional `nodriver` fallback with a throwaway profile for JS-only pages.
3. Extracts only what an anonymous visitor sees: OG metadata, TikTok rehydration JSON
   (`stats`, `desc`, `createTime`), Facebook embedded counters. No author ids, no member data.
4. Pushes records to `POST /ingest` (COALESCE upsert → refreshes engagement on rows the
   extension already captured) and statuses to `POST /seen`:
   `fetched` (ok) · `skipped` (login-wall / not-found / empty — **final, never retried with a session**) · `failed` (blocked / network — may retry).
5. Writes `layer-c-report.json` with block rate, per-platform outcomes, field fill rate on OK
   pages, fetcher mix, and a **GO / NO-GO** decision (GO = ≥10 eligible targets, block rate ≤ 20 %,
   ≥1 OK). Login walls are excluded from the block rate — a private group is out of scope by
   design, not a detection failure.

## Run (bizera-wsl)

```bash
cd services/lens-worker
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                   # 7 pure tests, no network

# 50-URL frontier spike against the running API (needs lens-api up, see deploy runbook)
export LENS_API_URL=http://localhost:7710 LENS_API_TOKEN=...   # from services/lens-api/.env
python -m worker.run --limit 50 --dry-run   # fetch + report only
python -m worker.run --limit 50             # also writes /ingest and /seen
python -m worker.run --limit 50 --browser   # enable nodriver fallback (needs Chrome/Chromium in WSL)

# Or without the API, from a file of permalinks:
python -m worker.run --urls urls.txt --dry-run
```

Exit code: `0` GO · `3` NO-GO · `1` nothing eligible · `2` bad args.

Pacing defaults to 4–9 s jittered between targets. Keep `--limit` ≤ 50 for the spike.

## Invariants (ADR-0004)

- Logged-out only: no cookies, no credentials, no session reuse; a login wall is final.
- Public content only; nothing about members; author ids never present → nothing to hash.
- One runtime (Python), two fetchers (`curl_cffi` primary, `nodriver` fallback). No Playwright/Crawlee/Scrapy.
- Not deployed as a container until GO; then add it to `services/lens-api/compose.yaml` as a
  scheduled job with `LENS_API_URL=http://lens-api:7710`.

## Layout

```
worker/frontier.py   eligibility (public / login-likely / unsupported) + target selection   [pure]
worker/extract.py    login-wall / block classification, TikTok + Facebook extraction, record shaping [pure]
worker/fetch.py      curl_cffi + nodriver fetchers (fresh session/profile per call)
worker/client.py     stdlib lens-api client (/health /seen /ingest)
worker/report.py     block-rate summary + GO/NO-GO                                             [pure]
worker/run.py        CLI
tests/               fixtures + 7 tests
```
