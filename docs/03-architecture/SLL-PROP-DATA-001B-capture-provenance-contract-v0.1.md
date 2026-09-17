# SLL-PROP-DATA-001B — Capture & Provenance Contract v0.1

- Parent: `SLL-PROP-DATA-001` (frozen) · Boundary: `001A` (frozen) · Roadmap: ADR-0005 Phase 5 → v0.7.0
- Status: **Implemented v0.1** (this document and PR #18 land together; freeze on merge)
- Date: 2026-09-17
- Contract family: **C01 Capture** — *original evidence remains traceable and immutable*

## 1. Purpose

Put L0 on the server and make every L1 row traceable to the exact payload it was parsed
from, without changing how evidence is acquired (ADR-0004) or what the extension sends for
records (SocialRecord v1 stays backward-compatible).

## 2. Lifecycle

```
browser response body ──► extension                     ──► lens-api            ──► LensDB
  (fetch/XHR/embedded)     store body (≤ maxRawBytes)         POST /raw               raw_captures   (L0)
                           payload_hash = sha256(body)        verify sha256, size     keyed by payload_hash
                           parse → records[]                  POST /ingest            records        (L1)
                           record.payload_hash = payload_hash upsert + capture_event  capture_events (L1 provenance)
                           record.content_hash = sha256(content fields)
```

Both pushes are idempotent; order between them does not matter (events reference hashes,
not row ids, so a record may arrive before its raw payload and still resolve later).

## 3. Identities

| Thing | Identity | Rule |
|---|---|---|
| Raw Capture (L0) | `payload_hash` = SHA-256 of the body **as stored** (after the extension's `maxRawBytes` cut; `truncated=true` records the cut) | Server recomputes and rejects mismatches; first capture context wins; duplicates are counted, never overwritten |
| Capture Event | `(record_key, payload_hash, captured_at)` | One per sighting of a source record; a post seen twice has two events and one record (I1) |
| Source Record (L1) | `platform:post_id` (unchanged) | Gains `content_hash`, `first_payload_hash` (never changes once set), `last_payload_hash`, `capture_count`, `protected` |
| Content hash | SHA-256 over `platform, post_id, record_type, NFC(text), created_at, permalink, media urls, hashtags, author_hash` | Engagement excluded on purpose: re-captures with unchanged content share a hash; a change in text/media is visible |

## 4. Endpoints (lens-api 0.5.0)

| Endpoint | Auth | Semantics |
|---|---|---|
| `POST /raw` `{source, version, captures[]}` | bearer | Each capture: `payload_hash, url, captured_at, body` (+ platform, method, status, source, page_url, truncated). Server verifies `sha256(body) == payload_hash` and `len(body) ≤ LENS_RAW_MAX_BYTES` (2 MB); returns `received / inserted / duplicate / rejected / rejected_hashes`. Rejected hashes are marked `synced=2` in the extension and not retried. |
| `POST /ingest` | bearer | Unchanged contract; records may carry `payload_hash` and `content_hash`; every record produces a capture event |
| `GET /provenance/{key}` | bearer | L1 row → events → whether the raw row exists and whether its body is still present |
| `GET /provenance` | bearer | Coverage metric: share of records whose `first_payload_hash` resolves to a stored raw capture (Phase 5 exit criterion: 100 % on a 7-day sample) |

## 5. Retention (D3, ordered)

| Layer | Setting | Action | Never |
|---|---|---|---|
| L0 body | `LENS_RAW_RETENTION_DAYS` (90) | `raw_captures.body := NULL`, `body_purged_at` set | Delete the row — hash + context are permanent |
| L1 rows | `LENS_RECORD_RETENTION_DAYS` (730; legacy `LENS_RETENTION_DAYS` honoured) | Delete `records` older than N days by post date (else capture date) **unless `protected`**; orphaned `seen_links` | Delete capture events (they hold only hashes) |
| L2/L3 | — | never purged | — |

`protected` is the hook for D3's "publish-referenced rows exempt": Phase 8's publish step sets
it; nothing in Phase 5 sets it, so the default behaviour is unchanged except that raw bodies
now age out before records. `POST /admin/purge?raw_days=&days=` runs both in order.

## 6. Extension (0.7.0)

- Dexie v3: `raw` rows carry `payload_hash`, `truncated`, `synced (0/1/2)`; upgrade back-fills `synced=0`.
- `sendRaw` setting (default **on**): after each successful `/ingest`, unsynced raw rows are
  pushed to `/raw` in batches of ≤ 25 rows / ≤ 4 MB (`planRawBatch`, unit-tested with Lao
  UTF-8 byte counting). Single rows over 4 MB are marked `synced=2` (never retried).
- Records carry `payload_hash` + `content_hash` at capture time (`contentHashInput`, tested).
- Local raw purge (`rawRetentionDays`, 30) is unchanged — the server now holds L0.
- Side panel: "Send raw evidence (L0) with sync" toggle; stats show raw unsynced count; sync
  message reports raw pushes and raw errors.
- Store mode default stays `matched` (D2 staged to Phase 6).

## 7. Invariants honoured

I1 (evidence never deleted by dedup: duplicate payloads are counted, duplicate sightings are
events), I2 (nothing here is a claim), I7 (events are time-stamped; `first_payload_hash` is
immutable), I9 (no new outbound surface; `/raw` and `/provenance` are Social Lens-internal),
I10 (raw bodies are the same data the extension already held; no new personal data category).

## 8. Exit evidence (to record in STATUS.md at v0.7.0)

1. `GET /provenance` → `coverage = 1.0` after ≥ 7 days of capture with `sendRaw` on.
2. `POST /admin/purge?raw_days=0&days=0` is a no-op; a dry run with real settings deletes no
   `protected` row (none exist yet) and no record referenced by an event newer than retention.
3. Extension sync latency within ± 10 % of v0.6.0 on the same group (compare "Pushed N
   records" round-trip in the panel).

## 9. Out of scope (next units)

Classification/claims (001C), geography (001D), listing clusters/entity resolution (001E),
DDL for L2/L3 (001F), publish/protected marking (001G/H).
