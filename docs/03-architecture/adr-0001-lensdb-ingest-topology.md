# ADR-0001: LensDB and Ingest API deployment topology

- Status: Accepted
- Date: 2026-09-16
- Deciders: OP-Vily
- SDLC gate: 3 (Architecture Design) → informs 4 (Detailed Solution Design)

## Context

BST Social Lens captures Facebook/TikTok signals in a browser extension whose only store today is IndexedDB (per browser, per machine). To make the data durable, queryable, shareable across capture accounts/machines, and consumable by the Social Lens Console and BST agents, we need a server tier. The question raised: should LensDB and the FastAPI service be separated, and where should they run?

The environment already runs `bizera-wsl` (WSL2 + Podman) hosting the BST MCP Hub (FastAPI Windows↔WSL bridge), so a hosting pattern and port discipline exist.

## Decision

Adopt a **three-tier** data architecture and host the server tier on `bizera-wsl` under Podman, in one pod with **separate containers**.

1. **Edge store (IndexedDB/Dexie)** — write-ahead capture buffer inside the extension. Not the system of record; the `synced` flag marks what has reached the server.
2. **LensDB (PostgreSQL 16)** — system of record. Container `lens-db`, **no published ports**, reachable only on the pod network, data on a named volume `lens-pgdata`.
3. **Ingest API (FastAPI)** — stateless HTTP boundary. Container `lens-api`, published on `127.0.0.1:7710` only (WSL2 forwards Windows `localhost`). The extension and the Console talk only to this.

Supporting decisions:

- **Separate `lens-api` and `lens-db` containers** so the API can be redeployed without touching the database, the DB volume is backed up independently, and Postgres can later scale/move without an API rewrite.
- **Do not reuse the MCP Hub process.** Share the host and Podman network, not the container — different lifecycle and blast radius.
- **Dedup in SQL** via `INSERT … ON CONFLICT (key) DO UPDATE`, keyed on `key = "${platform}:${post_id}"`, so concurrent syncs from multiple capture accounts stay consistent. Engagement/text refresh with `COALESCE`; `captured_at` takes `GREATEST`; `created_at`/`first_seen` preserved.
- **The Console reads through `lens-api`, never Postgres directly** — one writer, one reader process; no DB credentials in a published artifact.
- **Auth**: bearer token now (`LENS_API_TOKEN`); migrate to short-lived tokens issued by MCP Hub when the Console and agents both consume (Phase 4).
- **The extension never depends on the API being up** — it degrades to local-only capture + manual export.

## Consequences

Positive: durable shared store; clean backup/restore and reproducible deploy consistent with the rest of BST; the extension stays offline-resilient; the ingest contract is fixed early (`{source, version, records[]}`), unblocking the Console.

Negative / follow-ups: one more service to operate on `bizera-wsl`; a `/mcp` adapter on `lens-api` is deferred to Phase 4 for MCP Hub proxying; a Lao-aware full-text config is deferred (pg_trgm used for now).

## Alternatives considered

- **Fold LensDB into the extension permanently** — rejected: per-browser data, no cross-machine sharing, no server-side querying for the Console/agents.
- **Console reads Postgres directly** — rejected: leaks DB credentials into a publishable artifact and creates a second writer path.
- **Register ingest inside MCP Hub now** — deferred: couples the ship date to Hub work; ship standalone, add a `/mcp` adapter in Phase 4.
