# BST Social Lens — repository status snapshot

**This file is the auditable engineering truth for the repository.** It is updated by PR
only, records verifiable facts (SHAs, versions, gate states, evidence), and points to the
artefacts that prove them. Richer working context lives in the Claude Project ("BST Social
Lens" → `status/`), which is a working aid, not a source of truth.

| Field | Value |
|---|---|
| Snapshot date | 2026-09-16 |
| `main` | `51e2636` — "Merge Social Lens v0.5.0 into main" |
| Latest release | v0.5.0 (extension 0.4.0 · parsers 0.3.0 · lens-api 0.3.0 · Console) |
| Next release | v0.6.0 — extension 0.6.0 (Layer B assisted navigation), gated on PR #7 acceptance |
| Branch protection | Ruleset `main-protection` (active): PR required, conversation resolution, required checks `extension · check / test / build` + `lens-api · pytest` (strict), no force-push, no deletion, 0 approvals (no independent reviewer yet) |
| CI | `.github/workflows/parser-health.yml` — lands with PR #9; until then `main` has no workflow |

## SDLC gate status (AGENTS.md §5)

| Gate | State | Evidence |
|---|---|---|
| 1–2 PRD / decomposition | pending | — |
| 3 Architecture | done | ADR-0001 … ADR-0003 on `main`; ADR-0004 in PR #6 |
| 4 Detailed design | done | `src/lib/types.ts` schema v1; `services/lens-api/app/schema.sql` |
| 5 Security / compliance | pending | Lao EDPL 2017 mapping + ToS exposure register not yet authored; ADR-0004 §Context 2 records the legal posture as risk context |
| 6 Implementation planning | pending | — |
| 7 Development | active | extension 0.4.0 on `main`; 0.6.0 in PR #7; lens-worker spike in PR #8 |
| 8 Engineering verification | active | `main`: 28 vitest + 8 pytest. With PRs #6–#9: 42 vitest + 8 lens-api + 16 lens-worker pytest |
| 9–14 | not started | — |

## Open pull requests

| PR | Branch | Head | Disposition | Acceptance contract |
|---|---|---|---|---|
| #9 | `ci/parser-health` | `fa5282e` | approve — merge first | CI green on PR (extension, lens-api; lens-worker skipped-not-present) |
| #6 | `docs/adr-0004` | `45198df` | approve after #9 + checks | docs-only |
| #7 | `feat/assisted-navigation` | `8161af7` | code approved; operational acceptance pending | Lao-group A/B: assist off vs on, ≥ 2× comment records, both counts reported on the PR |
| #8 | `spike/layer-c-worker` | `ac7bc55` | **hold** | Evidence gate GO from a ≥ 50-target frontier run with LensDB baseline (`layer-c-report.json` attached to the PR); until then no `/ingest` or `/seen` writes are possible (machine-enforced) |

## Active evidence gates

1. **Layer B (PR #7)** — human-run A/B on a real Lao property group with the secondary account. Owner: OP-Vily. Blocked by: nothing (extension builds from the branch).
2. **Layer C (PR #8)** — `python -m worker.run --limit 50` against a live lens-api; decision GO / NO-GO / INCONCLUSIVE computed by `services/lens-worker/worker/report.py` (sample ≥ 50 from frontier, block ≤ 20 %, usable ≥ 60 %, incremental value vs baseline). Blocked by: lens-api deployment (below).

## Blockers

| Blocker | Impact | Reference |
|---|---|---|
| Podman networking on bizera-wsl (slirp4netns `/dev/net/tun`; netavark iptables on WSL2 kernel 6.6.114) | lens-api + LensDB not deployed → no ingest sync, no Console on live data, Layer C experiment cannot run | `docs/10-release-production/local-deploy-attempt-2026-09-16.md` (local note, untracked) · `docs/10-release-production/deploy-runbook.md` |

## Authoritative references

- Architecture: `docs/03-architecture/README.md` (ADR index)
- Tests: `tests/` (vitest), `services/lens-api/tests/`, `services/lens-worker/tests/`; fill-rate thresholds `tests/parser-health.thresholds.json` (PR #9)
- Change history: `CHANGELOG.md`
- Operating rules: `AGENTS.md`

## How to update

Change this file in the same PR that changes the fact it records (a release, a gate state,
a merged evidence result). Never edit `main` SHA or evidence lines from memory — copy them
from `git`, the PR, or the attached report.
