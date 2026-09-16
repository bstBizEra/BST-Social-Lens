# Evidence — Podman networking blocker on bizera-wsl (2026-09-16)

Classification: **operator-reported, sanitised.** Transcribed from the operator's local
deployment note (`docs/10-release-production/local-deploy-attempt-2026-09-16.md`, kept
untracked because it references local log paths). No credentials or hostnames beyond
`bizera-wsl` appear here. Build logs remain local under `/tmp/bst-social-lens-*deploy.log`.

## Environment observed
- Checkout `51e2636` (v0.5.0 merge); local `v0.5.0` tag present.
- Podman 4.6.2 — no `podman compose` subcommand; standalone `/usr/local/bin/podman-compose` installed.
- WSL2 kernel `6.6.114.1-microsoft-standard-WSL2+`.
- `services/lens-api/.env` created (gitignored) with independently generated token and DB password.

## Symptoms
| Attempt | Command | Failure |
|---|---|---|
| Rootless | `podman-compose -p bst-social-lens up -d --build` | image build (pip-install step): slirp4netns could not open `/dev/net/tun` |
| System (root) | same | netavark could not initialise the iptables NAT table; `ip_tables` module unavailable for the running kernel |
| Module load | `modprobe tun` | module unavailable for the running kernel |

Result: no application containers started; `http://localhost:7710/health` unreachable from Windows.

## Not attempted (deliberate)
WSL restart / kernel or module changes — other services were running on the instance.

## Next action
Repair WSL kernel modules (`tun`, `ip_tables`) or switch Podman network backend, rerun Compose,
then verify `/health`, authenticated endpoints, persistence, and Console access from Windows
per `docs/10-release-production/deploy-runbook.md` §2.
