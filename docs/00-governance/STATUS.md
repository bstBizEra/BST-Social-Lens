# BST Social Lens — repository status snapshot

**Point-in-time controlled snapshot, not live state.** Git history tells you what is
current; this file tells you what was *verified* at a specific snapshot base. It is updated
by PR only, records verifiable facts (SHAs, versions, gate states, evidence) as observed at
the snapshot base, and points to the artefacts that prove them. Later merges do not
invalidate a snapshot — they make a newer snapshot necessary when the change is material.
Richer working context lives in the Claude Project ("BST Social Lens" → `status/`), which
is a working aid, not evidence.

| Field | Value |
|---|---|
| Snapshot observed at | 2026-09-18T07:00:00+07:00 (2026-09-18T00:00:00Z) |
| Snapshot base SHA | `2fed70a` — `main` as observed when this snapshot was prepared ("Merge pull request #48 from bstBizEra/feat/hk-control-center") |
| Snapshot PR | #49 |
| Previous snapshot | base `f8e3816`, PR #36 |
| Status scope | Repository state as observed at the snapshot base; PRs listed are those open at that moment |
| Latest release | v0.5.0 — GitHub Release exists (extension 0.4.0 · parsers 0.3.0 · lens-api 0.3.0 · Console). Since the release `main` carries: parser-health CI (#9), ADR-0004 (#6), STATUS snapshot (#10), runbook §2a + ops (#11–#13), MCP adapter (#15, lens-api 0.4.0), retention (#16), SLL-PROP-DATA-001/001A + ADR-0005 + ROADMAP (#17), Phase 5 capture & provenance (#18, lens-api 0.5.0, 001B), STATUS snapshot (#19), 001C/001D contracts (#20), RULE_V1 rules (#21), `extract.*` DDL + schema-lint (#22), extraction run loop + L2 API + MCP tools (#23, lens-api 0.6.0), 001D geo text path + golden tooling + snapshot (#24, lens-api 0.6.1), 001E contract (#25), 001E pure building blocks — permalink rules, text similarity, `MATCH_V1` (#26), clusters + blocking (#27), 001F draft + lint R6–R8 + snapshot (#28), 001G/001H contracts (#29), DQ scorer + `audit.events` (applied) + bundle check (#30), review CSV round-trip + `/quality/stats` + snapshot (#31, lens-api 0.6.2), PostGIS point path + Lao Data Map importer (#32), 001E resolution wired behind `LENS_RESOLUTION_ENABLED` (#33, lens-api 0.6.3), STATUS snapshot (#34), match review queue + DQ grades on snapshots + `/admin/quality/recompute` (#35, lens-api 0.6.4), STATUS snapshot (#36), extension sync fixes — host permission, byte-accurate raw truncation, raw outcome (#37), RULE_V1 1.0.1 from the first real data (#38), sightings once per context + capture targets + `capture_events.context` (#39), SLL-DATA-HK-001 contract + `/housekeeping/status` (#40, lens-api 0.6.5), `housekeeping.*` persistence + `/lineage/{id}` (#41, 0.6.6; fix #42), required keywords (#43), Housekeeper Actor + `/raw/needed` (#44, 0.6.7; fixes #45–#47), Data Control Center Console tab (#48) |
| Next release | v0.7.0 — extension 0.7.0 = Phase 5 provenance (on `main`) + Layer B assisted navigation (PR #7, gated on A/B). Version fields on `main` still read 0.4.0; the bump lands with #7 (see *Release reconciliation*). v0.8.0 (Phase 6) follows once 001C/001D freeze on the golden gates |
| Architecture baseline | `SLL-PROP-DATA-001` parent (frozen), `001A` Domain & Data Boundary v0.1 (frozen, D1–D7 adopted), `001B` Capture & Provenance Contract v0.1 (frozen at #18); `001C` Property Extraction and `001D` Geo-Normalisation v0.1 (**draft for freeze**, implemented; freeze on the golden-fixture gates); `001E` Entity Resolution v0.1 (**draft for freeze**, all algorithmic parts implemented as pure modules, unwired); `001F` Canonical Database Design v0.1 (**draft**, `schema_market.sql` written/validated/linted, not applied); `001G` Quality & Review and `001H` Publication v0.1 (**draft for freeze**; DQ scorer, rules, `audit.events`, bundle check implemented); ADR-0005 **Accepted** — Phases 5–9 in `docs/01-product-requirements/ROADMAP.md` |
| Branch protection | Ruleset `main-protection` (active): PR required, conversation resolution, required checks `extension · check / test / build` + `lens-api · pytest` (strict), no force-push, no deletion, 0 approvals (no independent reviewer yet) |
| CI | `.github/workflows/parser-health.yml` on `main`. Required checks report on every PR; the `lens-api · pytest` job also runs `scripts/schema_lint.py` (R1–R8: L2 additive-only, 001A vocabulary, confidence NOT NULL, no raw contact in claims, no price on market properties, snapshots versioned, decisions append-only); `lens-worker · pytest` is skipped (not green) while the worker is absent on the base |
| Deployment | lens-api runs container-free on bizera-wsl as `bst-lens-api.service` (systemd, runbook §2a) against local PostgreSQL; running `2fed70a` (lens-api 0.6.7) — `extract.*`, `geo.*`, `audit.*`, `housekeeping.*` applied additively at startup; `schema_market.sql` **not applied** (`LENS_RESOLUTION_ENABLED` unset → `/market/*`, `/resolution/stats`, `/admin/quality/recompute` return 404); extraction loop (15 min) and Housekeeper loop (30 min, Actor on) active; Console with the Data Control Center tab served at `/`. **First real capture data landed 2026-09-17T16:02Z: 333 records** (332 pushed from one Facebook group by extension 0.7.0), all extracted on RULE_V1 1.0.1; raw evidence coverage 0.3 % (170 distinct payload bodies outstanding, marked in `housekeeping.raw_needed`, awaiting extension 0.7.4+ sync); gazetteer empty (228 observations `TEXT_ONLY`). PostGIS not yet installed (001D G1). Windows→WSL reachability fixes recorded in runbook §4a (WSL idle timeout, Hyper-V loopback, CORS) |

## SDLC gate status (AGENTS.md §5)

| Gate | State | Evidence |
|---|---|---|
| 1–2 PRD / decomposition | active | `docs/01-product-requirements/ROADMAP.md` (ADR-0005 phases → releases); SLL-PROP-DATA-001 parent + 001A/001B on `main` |
| 3 Architecture | done | ADR-0001 … ADR-0005 (`docs/03-architecture/README.md`); 001A frozen (L0–L3 layers, invariants I1–I10) |
| 4 Detailed design | done for Phases 5–6; drafted for 7–9 (001E–001H); 001F DDL exercised end-to-end on disposable DBs behind the flag; **SLL-DATA-HK-001** (cross-cutting stewardship control plane) drafted 2026-09-18 with §11 four-week freeze clock running from the base | `src/lib/types.ts` schema v1 (+ `payload_hash`/`content_hash`); `services/lens-api/app/schema.sql` (L0/L1), `schema_extract.sql` (L2 extraction, 001F subset) — physical tables carry the 001C CHECKs (vocabularies, 0..1 confidence, no `raw_value` in claims JSON) |
| 5 Security / compliance | **parked by owner decision** | Lao EDPL 2017 mapping + ToS exposure register not authored (PR #14 closed). ADR-0004 §Context 2 records the legal posture as risk context |
| 6 Implementation planning | active | ROADMAP.md phase table; Phase 6 code complete, waiting on operator data for the gates; Phase 7 complete end-to-end behind `LENS_RESOLUTION_ENABLED` (clusters → blocking → `MATCH_V1` → candidates/decisions → market properties + stats snapshots on the 001F draft DDL), calibration pending data; Phase 8 DQ/audit/review-CSV (all three queues)/bundle-check/DQ snapshots built; HK-001 build order (1) persistence (2) lineage (3) Actor (4) Control Center complete — remaining work is data-gated (golden labels, gazetteer, reviewed sample) |
| 7 Development | active | `main`: extension source at Phase 5 + keyword groups + sightings/targets + required keywords + host-permission/raw re-request (version field 0.4.0; operator build 0.7.5 = main + Layer B), lens-api 0.6.7 (`/raw`, `/provenance`, `/raw/needed`, `/mcp` 17 tools, `/housekeeping/status|findings`, `/admin/housekeeping/run`, `/lineage/{id}`, retention, RULE_V1 → `extract.*`, GEO_RULE_V1 → `geo.*` incl. PostGIS point path when present, `/observations`, `/extract/stats`, `/geo/*`, `/quality/stats`; flag-gated `/admin/resolve`, `/market/properties`, `/resolution/stats`, `/admin/quality/recompute`), `app/resolution/` (pure modules + `store.py`/`service.py`), `app/quality/`, `app/publish/`, `scripts/review.py` (extraction, location and match queues), `scripts/geo_import.py`, `scripts/install-postgis.sh`, `app/housekeeping/` (status, store, actor, lineage); Layer B (0.7.5 integration incl. dialog dismissal) in PR #7; lens-worker spike in PR #8 |
| 8 Engineering verification | active | `main` at base: 50 vitest passed + 1 skipped + 159 lens-api pytest passed + 15 skipped (golden gates awaiting labelled data; live DB tests behind `LENS_TEST_DSN`), CI-enforced (#48 run). Live DB tests (schema, extraction, geo incl. `ST_Within`, review round-trip on all three queues incl. a CSV-confirmed MERGE, 001E resolution end-to-end with the flag on, DQ snapshot recompute, Housekeeper persistence + Actor + lineage on both flag states) passed on a disposable PostgreSQL 16 + PostGIS 3 during preparation of PRs #22–#24, #28, #30–#48 |
| 9–14 | not started | — |

## Phase 5 exit evidence (001B §8) — observed on the live service at base

Run: `services/lens-api/scripts/phase5-smoke.sh` on bizera-wsl, 2026-09-17T09:07Z, against
`bst-lens-api.service` running `3189f51` (fresh LensDB, 0 records before the run).

| Check | Observed | Result |
|---|---|---|
| `POST /raw` new / duplicate / bad hash | `inserted:1` → `duplicate:1` → `rejected:1` with the hash echoed in `rejected_hashes` | pass |
| `POST /ingest` with `payload_hash` → capture event | `GET /provenance/facebook:smoke-phase5-1`: `capture_count:1`, `first_payload_hash == last_payload_hash`, one event with `raw_present:true, body_present:true, body_bytes:46` | pass |
| `GET /provenance` coverage | `records:1, records_resolved_to_raw:1, coverage:1.0` | pass (§8.1 requires the same on a 7-day capture sample — **pending**, needs extension 0.7.0 running with `sendRaw` on) |
| `POST /admin/purge?raw_days=0&days=0` | `raw_bodies_purged:0, records:0, seen_links:0` | pass (§8.2 no-op) |
| §8.3 sync latency within ± 10 % of v0.6.0 | not yet measured | **pending** (operator, with the 0.7.0 build) |

## Phase 6 evidence (001C §10, 001D §9) — observed at base

| Check | Observed | Result |
|---|---|---|
| Live extraction on `bst-lens-api.service` @ `67e7e18`+ (2026-09-17T16:12Z, first real data) | 332 records → 332 observations, 1 884 claims, 2.7 s; classes SALE 155 / NON_PROPERTY 57 / WANTED 54 / UNCERTAIN 29 (8.7 %) / PRICE_DISCUSSION 28 / RENT 8; `claims_without_confidence: 0`; RULE_V1 1.0.1 re-extraction automatic on version bump; after 1.0.1: currency-less prices 63 → 36, price_range exceptions 24 → 10, area_range 9 → 0 | pass (§10.5 mechanics on a 333-record sample; precision measurement **pending** golden labels — `_labelling/golden-*.csv` exported 2026-09-18) |
| Determinism (§10.2) | `tests/test_extract_rules.py::test_determinism_byte_identical` | pass |
| No-fact lint (§10.3) | `scripts/schema_lint.py` in CI; negative tests per rule | pass |
| Keyword parity (§10.4) | `tests/keyword-groups-parity.test.ts` (fails on a single-term drift — verified) | pass |
| Golden fixtures (§10.1, 001D §9.2) | not yet labelled | **pending** (operator, C5; `scripts/golden.py` in PR #24) |
| 001D §9.5 precision assignment | `GET /geo/stats.precision_assigned_share` live on `50978ca`; no gazetteer imported yet so no location resolved beyond `TEXT_ONLY` | **pending** (Lao Data Map import) |
| 001E §11 (Phase 7) | full run wired and live-tested behind the flag (`tests/test_resolution_live.py`: 4 advertisers + 1 re-post of one parcel → one property keeping all 5 asking prices; decoy separate; idempotent re-run; human `UNLINKED` survives `force`); calibration candidate recorded (`test_multi_agent_same_land`) | **pending** reviewed sample (needs real observations); production flag stays off until freeze |
| 001G §9 (Phase 8) | DQ scorer deterministic (§9.1 CI test); property `dq_grade` on every statistics snapshot; CSV round-trip on extraction, location and match queues with zero silent skips and one audit row per action verified live on a disposable DB (§9.2/§9.3 mechanics; match CONFIRM = MERGE, absorbed property `SUPERSEDED`, nothing deleted) | **pending** two-week live trial on real queues |
| HK-001 §11 (stewardship) | Housekeeper live since 2026-09-18: runs persisted (`housekeeping.runs`), watermarks + 6 reconciliation checks per run, findings upserted/resolved, Actor took MARK_RAW_NEEDED (170 hashes) with one audit row; a failed first attempt (CardinalityViolation) was recorded and left the finding OPEN as designed; `/lineage/{id}` answers for record/observation/decision/property; Control Center renders counts only | **pending** four consecutive weeks without a false CRITICAL (clock started at base) |

## Pull requests observed at snapshot

Merged into the base since the previous snapshot: #36–#48 (merge `2fed70a`).

| PR | Branch | Head | Disposition | Acceptance contract |
|---|---|---|---|---|
| #49 | `docs/status-snapshot-2fed70a` | this PR | approve | snapshot refresh only |
| #7 | `feat/assisted-navigation` | `c6051c7` (0.7.5 integration: main + Layer B + dialog dismissal) | code approved (review rounds 1–2); operational acceptance pending | Lao-group A/B: assist off vs on, ≥ 2× comment records, both counts reported on the PR |
| #8 | `spike/layer-c-worker` | `ac7bc55` | code approved; **evidence hold** | Evidence gate GO from a ≥ 50-target frontier run with LensDB baseline (`layer-c-report.json` attached to the PR); no `/ingest` or `/seen` writes possible until then (machine-enforced) |

## Active evidence gates (at snapshot)

1. **Layer B (PR #7)** — human-run A/B on a real Lao property group with the secondary account. Owner: OP-Vily. Blocked by: nothing — operator build 0.7.5 (`dist/bst-social-lens-0.7.5-edge.zip`, loaded folder `dist/edge-mv3-0.7.0/`) includes the post-dialog dismissal fix; not committed.
2. **Layer C (PR #8)** — `python -m worker.run --limit 50` against the live lens-api; decision GO / NO-GO / INCONCLUSIVE computed by `services/lens-worker/worker/report.py`. Blocked by: nothing on infrastructure any more (lens-api is live via systemd); needs an operator-run frontier.
3. **Phase 5 §8.1 / §8.3** — raw coverage to 100 % (extension 0.7.4+ answers `/raw/needed`; 170 bodies outstanding at base), then the 7-day coverage sample and latency comparison; owner OP-Vily.
4. **Phase 6 golden gates** — ≥ 100 labelled records + ≥ 100 labelled locations (`_labelling/golden-records.csv` 150 rows, `golden-locations.csv` 126 rows exported 2026-09-18 → label → `build`); owner OP-Vily. 001C/001D freeze when `test_golden_fixture_gate` and `test_golden_locations_gate` pass in CI.
5. **001D G1** — PostGIS on the bizera-wsl cluster (`bash services/lens-api/scripts/install-postgis.sh` + restart); unblocks the point path (`ST_Within`). Owner OP-Vily.
6. **Lao Data Map import** — `scripts/geo_import.py convert → check → post`; until then every location resolves `TEXT_ONLY`. Owner OP-Vily.
7. **001E reviewed sample** — ≥ 50 market properties reviewed (CSV round-trip) once real observations exist; calibrates `MATCH_V1`; then `LENS_RESOLUTION_ENABLED=1` in production. Owner OP-Vily.

## Release reconciliation

`main` carries Phase 5 code but its version fields still read 0.4.0 (the 0.6.0 bump lives on
PR #7). Decision: v0.6.0 is **skipped as a tag**; the next tag is **v0.7.0** cut after #7
merges, with `package.json` / `wxt.config.ts` bumped to 0.7.0 in that merge (or a one-line
follow-up). The `CHANGELOG.md` 0.6.0 section is folded into 0.7.0 at that time.

## Blockers

| Blocker | Impact | Evidence class | Reference |
|---|---|---|---|
| Podman networking on bizera-wsl (slirp4netns `/dev/net/tun`; netavark iptables on WSL2 kernel 6.6.114) | Compose path unusable; **mitigated** by the container-free systemd run (runbook §2a). No longer blocks ingest, Console, or Layer C | **Operator-reported, sanitised** | `docs/10-release-production/evidence/2026-09-16-podman-networking.md` · `docs/10-release-production/deploy-runbook.md` §2a |

## Authoritative references

- Architecture: `docs/03-architecture/README.md` (ADR index; SLL-PROP-DATA-001 family)
- Roadmap: `docs/01-product-requirements/ROADMAP.md`
- Tests: `tests/` (vitest), `services/lens-api/tests/`, `services/lens-worker/tests/`; fill-rate thresholds `tests/parser-health.thresholds.json`
- Change history: `CHANGELOG.md`
- Operating rules: `AGENTS.md`

## How to update

Prepare a new snapshot when a material fact changes (a release, a gate state, a merged
evidence result, a PR landing): set *Snapshot observed at* to an offset-aware timestamp,
*Snapshot base SHA* to the `main` SHA observed at preparation (`git rev-parse --short
origin/main`), and *Snapshot PR* to the PR carrying the update. Never write a SHA or an
evidence line from memory — copy it from `git`, the PR, or the attached report. The
snapshot base is by construction one commit behind the merge that lands it; that is the
intended semantics, not an error.
