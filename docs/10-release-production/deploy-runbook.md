# BST Social Lens — Deploy & Operations Runbook

- Owner: OP-Vily / BizEra
- SDLC gate: 10 (Release / Production), feeds 11 (Operations & Resilience)
- Scope: stand up the whole system — extension (Edge/Chrome) + lens-api + LensDB + Console — on `bizera-wsl`, operate it safely, back it up, and roll back.
- Applies to: v0.5.0 (extension 0.4.0 + server tier + Console)

This is a checklist. Do the phases in order; each ends with a verification you can see.

---

## 0. Prerequisites

- `bizera-wsl` (WSL2) with **Podman** + `podman compose` (or Docker + compose).
- Node ≥ 20 / npm ≥ 10 (only needed to build the extension zips; already built in `dist/`).
- Microsoft Edge or Chrome ≥ 116 (side panel requires 116+).
- A **secondary** Facebook/TikTok account for capture (never the primary business admin account).
- Repo at `C:\laragon\www\BST-Social-Lens` (Windows) = `$HOME/mnt/BST-Social-Lens` (WSL mount).

---

## 1. Merge the PR stack into `main`

Four stacked PRs, each based on the previous. Merge **in order** so the diffs stay clean:

| Order | PR | Branch | Brings |
|---|---|---|---|
| 1 | #1 | `feat/lens-api-ingest` | server tier (lens-api + LensDB) |
| 2 | #2 | `feat/keyword-frontier` | keyword ingestion, comments, seen frontier |
| 3 | #3 | `feat/autonomous-mode` | auto-scroll mode |
| 4 | #4 | `feat/console` | Social Lens Console |

```bash
# from the repo, on the machine that has GitHub credentials (Windows)
gh pr merge 1 --merge --delete-branch=false
gh pr merge 2 --merge --delete-branch=false
gh pr merge 3 --merge --delete-branch=false
gh pr merge 4 --merge --delete-branch=false
git checkout main && git pull
```

Review each PR first if you want gate-9 sign-off. After #4 merges, `main` has everything at v0.5.0.

Verify: `git log --oneline -6` on `main` shows the four feature commits; `ls services/lens-api console` exist.

---

## 2. Deploy lens-api + LensDB (bizera-wsl, Podman)

```bash
cd "$HOME/mnt/BST-Social-Lens/services/lens-api"    # or C:\laragon\www\BST-Social-Lens\services\lens-api
cp .env.example .env
# edit .env — set a long random token:
#   LENS_API_TOKEN=$(openssl rand -hex 24)
#   POSTGRES_PASSWORD=<another strong value>
podman compose up -d --build
```

What comes up:
- `lens-db` — PostgreSQL 16, **no published ports** (pod network only), data on volume `lens-pgdata`.
- `lens-api` — FastAPI on `127.0.0.1:7710` (WSL2 forwards Windows `localhost:7710`). Serves the Console at `/`.

Verify:
```bash
curl -s http://localhost:7710/health          # {"status":"ok","db":true,"records":0}
curl -s http://localhost:7710/ | head -c 200  # Console HTML
```
If `/health` shows `db:false`, `lens-db` isn't healthy yet — `podman compose logs lens-db` and retry in ~10s.

### 2a. Container-free fallback (Podman networking unavailable)

Use this when Podman cannot bring up the pod network (symptoms and evidence:
`evidence/2026-09-16-podman-networking.md`). It runs the **same** lens-api code under
`uvicorn` against a local PostgreSQL in WSL — enough for ingest sync, the Console, and the
Layer C experiment. Bind stays `127.0.0.1:7710`; nothing else changes for the extension or
the worker. Return to §2 once Podman is repaired; the schema is identical, so `pg_dump` /
restore (§8) moves the data.

```bash
# 1. Local PostgreSQL (Ubuntu/Debian WSL; once)
sudo apt-get install -y postgresql postgresql-contrib
sudo service postgresql start                          # WSL has no systemd by default
sudo -u postgres psql -c "CREATE USER lens WITH PASSWORD '<strong value>';"
sudo -u postgres psql -c "CREATE DATABASE lens OWNER lens;"
sudo -u postgres psql -d lens -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 2. lens-api in a venv
cd "$HOME/mnt/BST-Social-Lens/services/lens-api"      # or /mnt/c/laragon/www/BST-Social-Lens/services/lens-api
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 3. Runtime config — same variables as .env, host points at localhost
export LENS_DB_DSN="postgresql://lens:<strong value>@127.0.0.1:5432/lens"
export LENS_API_TOKEN="$(openssl rand -hex 24)"        # keep it: the extension + worker need it
export LENS_CONSOLE_DIR="$(cd ../../console && pwd)"   # serves the Console at /
uvicorn app.main:app --host 127.0.0.1 --port 7710      # schema is applied idempotently on startup
```

Verify exactly as above (`/health` → `db:true`). Run it under `tmux`/`screen` or a WSL
`nohup` so it survives the shell; it is a fallback, not the production topology. Postgres
listens on `127.0.0.1` only by default — leave it that way.

Backup in this mode: `pg_dump -U lens -h 127.0.0.1 lens > lens_$(date +%F).sql`.

---

## 3. Load the extension

Edge: `edge://extensions` → enable **Developer mode** → **Load unpacked** → `dist/edge-mv3`.
Chrome: `chrome://extensions` → Developer mode → Load unpacked → `dist/chrome-mv3`.

(Rebuild first only if you changed source: `npm install && npm run build && npm run build:edge`.)

Verify: the card shows **BST Social Lens 0.4.0**, no errors. Pin it to the toolbar so the side-panel button is one click (puzzle icon → pin).

---

## 4. Connect the extension to lens-api

1. Open a Facebook group or TikTok page, then open the side panel (toolbar icon).
2. **BST Ingest API** section: Ingest URL `http://localhost:7710/ingest`, Bearer token = your `LENS_API_TOKEN`.
3. Turn on **Auto-sync every 5 min** (or use **Sync now**).

Verify: after a capture, `curl -s -H "authorization: Bearer $LENS_API_TOKEN" http://localhost:7710/stats` shows non-zero `total`.

---

## 5. Configure keywords & capture mode

Side panel → **Keywords & filtering**:
- Include: `ດິນ, ຂາຍ, ເຊົ່າ, ລາຄາ, ບ້ານ, ເມືອງ, ແຂວງ` (tune per target).
- Store mode: **matched** (lead list) — switch to **all** only when exploring a new group.
- **Capture comments**: on.

Matching is Lao-aware (NFC, substring), so `ຂາຍດິນ` matches both `ຂາຍ` and `ດິນ`. A matching comment promotes its parent post.

---

## 6. Capture — manual and autonomous

- **Manual**: open a group / hashtag / video and scroll; the badge shows records · payloads.
- **Autonomous**: side panel → **Autonomous mode** → **Start auto-scroll**. Jittered 2.5–5s pacing; auto-stops at caps (default 40 scrolls / 10 min) or end of feed; **Stop** anytime. Runs only while the panel and tab are open.

**TikTok note:** search/hashtag feeds are login-gated — sign in to TikTok in the capture browser (secondary account). Video pages + their comments capture without login.

Account safety: secondary account, small runs, keep caps modest. No background/headless browsing.

---

## 7. Open the Console

Browse to **http://localhost:7710/** (served by lens-api, same-origin — no CORS). If you set a token, paste it in the Console's Bearer field and **Connect**.

You get: KPI tiles, keyword facets, source-type + date filters, a Lao-aware leads table with keyword highlighting, top-sources bars, and CSV export.

---

## 8. Backup & restore (gate 11)

Back up the Postgres volume:
```bash
podman exec bst-social-lens_lens-db_1 pg_dump -U lens lens > lens_$(date +%F).sql
# restore:  podman exec -i <lens-db> psql -U lens lens < lens_YYYY-MM-DD.sql
```
Raw payloads auto-purge after `rawRetentionDays` (default 30) in the extension store; LensDB keeps normalized records indefinitely. Schedule the dump weekly.

---

## 9. Rollback

- **Extension**: load the previous release's unpacked zip (`releases` tab: v0.3.0 / v0.4.0) or `git checkout <tag>` and rebuild.
- **Service**: `podman compose down` (keeps the volume) → check out the previous tag → `podman compose up -d --build`. Data on `lens-pgdata` survives.
- **DB schema** is additive (idempotent `CREATE ... IF NOT EXISTS`); rolling the API back does not drop columns.

---

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| Records stay 0 while scrolling | Store mode is `matched` and nothing matched — widen keywords or switch to `all`. Confirm the on-page badge shows payloads climbing. |
| Badge shows payloads but 0 records | Keyword mismatch (expected) or a parser gap — side panel → **Raw payloads (NDJSON)** export and send it for a parser fix. |
| Sync error in side panel | Wrong ingest URL/token, or lens-api down — `curl /health`. |
| `/health` `db:false` | `lens-db` unhealthy — `podman compose logs lens-db`. Container-free mode: `sudo service postgresql status`, and check `LENS_DB_DSN`. |
| `podman compose up` fails at build/network (`/dev/net/tun`, netavark iptables, `modprobe tun`) | WSL kernel lacks `tun`/`ip_tables` — see `evidence/2026-09-16-podman-networking.md`; use §2a until the kernel/modules are repaired. |
| Console empty at localhost:7710 | Token mismatch, or `LENS_CONSOLE_DIR` missing in the image — rebuild with the repo-root build context. |
| Account checkpoint / lock | Stop autonomous runs; you're pacing too fast or on the primary account — use a secondary account and smaller caps. |
| Edge card shows an old version | Reload the unpacked extension, or toggle it off/on; a browser restart refreshes the version chip. |

---

## 11. Guardrails (do not skip)

- Post/comment content + engagement only; author IDs hashed by default. No member-profile aggregation (names + contacts + location).
- Respect platform ToS and the Lao PDR Law on Electronic Data Protection (2017). This tool is for market/community intelligence on content you can already see.
- Never use "scraper"/"crawler" in any store-facing copy.
- Real captured payloads (`tests/fixtures/_live/`) are gitignored — never commit them.

---

## 12. Deferred (Phase 4)

Assisted navigation — auto-opening matched permalinks and expanding hidden comment threads on the seen-link frontier — is not in v0.5.0 (higher account risk). The `lens-api /mcp` adapter for MCP Hub is also deferred. See ADR-0003.
