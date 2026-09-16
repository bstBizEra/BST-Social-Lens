# lens-worker — Layer C spike (logged-out, public permalinks)

Status: **spike** (ADR-0004 rule 5 — graduates to a service only on a GO decision).

## What it does

1. Pulls frontier rows with `last_status='seen'` from lens-api `GET /seen` (or a URL list).
2. Fetches each permalink **logged out** — fresh `curl_cffi` session impersonating Chrome
   (no cookies), optional `nodriver` fallback with a throwaway profile for JS-only pages.
3. Extracts only what an anonymous visitor sees: OG metadata, TikTok rehydration JSON
   (`stats`, `desc`, `createTime`), Facebook embedded counters. No author ids, no member data.
4. **Only on GO** (and never with `--dry-run`) pushes records to `POST /ingest` (COALESCE upsert →
   refreshes engagement on rows the extension already captured) and statuses to `POST /seen`.
   NO-GO / INCONCLUSIVE runs are report-only — "not graduated" is machine-enforced. Statuses:
   `fetched` (ok) · `skipped` (login-wall / not-found / empty — **final, never retried with a session**) · `failed` (blocked / network — may retry).
5. Writes `layer-c-report.json` with per-outcome/platform counts, field fill rate on OK pages,
   fetcher mix, and the **evidence gate** (ADR-0004 rule 5). GO requires **all** of:
   - **sample** — ≥ 50 targets attempted, sourced from the seen frontier (a constant in `report.py`, deliberately not a CLI flag);
   - **block rate** — (blocked + error) / eligible ≤ 20 %, eligible = attempts − login-wall − not-found
     (a private group is out of scope by design, not a detection failure);
   - **usable rate** — ok / eligible ≥ 60 % (so "1 OK + 49 empty" cannot pass);
   - **incremental value** — vs the LensDB baseline (`GET /records`), OK records add or refresh
     ≥ 1 field each on average with ≥ 50 % baseline coverage.
   Missing baseline or experiment-file input → **INCONCLUSIVE** (never GO); any failed criterion → **NO-GO**.

## Acquisition boundary

Every URL — frontier or experiment file — passes `frontier.validate_target()` before any network call:

```
HTTPS only → facebook.com / tiktok.com host allow-list → no IP literal, localhost, private,
link-local, credentials or non-443 port → recognised public permalink pattern → fetch
```

`--experiment-urls` is an **offline experiment input**, not an acquisition path: rejected lines are
listed in the report and the decision can never be GO.

## Run (bizera-wsl)

```bash
cd services/lens-worker
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                   # 7 pure tests, no network

# 50-URL frontier spike against the running API (needs lens-api up, see deploy runbook)
export LENS_API_URL=http://localhost:7710 LENS_API_TOKEN=...   # from services/lens-api/.env
python -m worker.run --limit 50 --dry-run   # fetch + report only
python -m worker.run --limit 50             # writes /ingest and /seen ONLY if the decision is GO
python -m worker.run --limit 50 --browser   # enable nodriver fallback (needs Chrome/Chromium in WSL)

# Offline experiment from a file of permalinks (boundary-checked; never GO):
python -m worker.run --experiment-urls urls.txt --dry-run
```

Exit code: `0` GO · `3` NO-GO · `4` INCONCLUSIVE · `1` nothing eligible · `2` bad args.

Pacing defaults to 4–9 s jittered between targets. Keep `--limit` ≤ 50 for the spike.

## Invariants (ADR-0004)

- Logged-out only: no cookies, no credentials, no session reuse; a login wall is final.
- Public content only; nothing about members; author ids never present → nothing to hash.
- One runtime (Python), two fetchers (`curl_cffi` primary, `nodriver` fallback). No Playwright/Crawlee/Scrapy.
- Not deployed as a container until GO; then add it to `services/lens-api/compose.yaml` as a
  scheduled job with `LENS_API_URL=http://lens-api:7710`.

## Layout

```
worker/frontier.py   acquisition boundary (validate_target), eligibility, target selection  [pure]
worker/extract.py    login-wall / block classification, TikTok + Facebook extraction, record shaping [pure]
worker/fetch.py      curl_cffi + nodriver fetchers (fresh session/profile per call)
worker/client.py     stdlib lens-api client (/health /seen /ingest)
worker/report.py     evidence gate (sample/block/usable/incremental value) → GO/NO-GO/INCONCLUSIVE [pure]
worker/run.py        CLI
tests/               fixtures + 16 tests
```
