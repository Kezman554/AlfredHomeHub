# Deployment

## Target Device — Raspberry Pi 5 (8GB), "Alfred"

| Field | Value |
|---|---|
| Hostname | `alfred` (resolves as `alfred.local` via mDNS) |
| User | `kezman554` |
| Static IP | `192.168.1.100` — DHCP reservation on the router, bound to MAC `2C:CF:67:B7:5F:21` (stable across reboots) |
| SSH | `ssh alfred` (alias in laptop `~/.ssh/config`) or `ssh kezman554@192.168.1.100` — passwordless key auth |
| OS | Raspberry Pi OS (Debian-based, aarch64, kernel 6.12.x) |
| Runtime | Docker + docker compose plugin; `kezman554` is in the `docker` group (no `sudo` for docker) |

> `.local` resolution fails from the laptop while a VPN is connected — disconnect the VPN if `alfred.local` won't resolve.

## Service Ports

| Port | Service | Notes |
|---|---|---|
| 8123 | Home Assistant | Host networking (device discovery) |
| 8200 | Vault API | Vault content + rolling to-do writes. `http://192.168.1.100:8200` — `/health`, `/chalkboard` (GET/POST, `/tick`, `/drop`, `/sweep`), `/daily-schedule`, `/daily-schedule/week`, `/shopping` (discovery GET/POST create, `/{list_id}` GET/POST add, `/tick`, `/drop`), `/inbox` (GET list, POST capture — body is `text/plain`) |
| 8300 | Kanban API | HTTP over the Kanban board's data layer — **data only, no board UI** (bookmark **`http://192.168.1.100:8300`**; it returns a JSON banner). Runs against a *copy* of the board DB; the laptop Electron app stays authoritative until the UI-port card. `/health`, `/stats`, `/projects`, `/projects/{id}` (+ `/workable`, `/done`, `/next-letter`, `POST /append`), `/cards/{id}` (+ `/status`, `/details`, `/notes`, `/prompt`, `/clear-dependencies`, `DELETE`), `/export/json`, `/export/summary` |

## Deploy Model

- Laptop is the source of truth: develop here, push to GitHub (`git@github.com:Kezman554/AlfredHomeHub.git`).
- The Pi **pulls** — repo lives on the Pi at `~/projects/AlfredHomeHub`.
- Services (including Home Assistant) run as Docker containers; build/select ARM64 images.

## Kanban API (two-repo build)

The Kanban API wraps the Kanban board's data layer, which lives in a **separate
repo** (`git@github.com:Kezman554/Kanban_App.git`) — its `operations.js` is
wrapped in place, never copied here, so it can't drift. The service is defined
in this repo's compose file but **builds from the Kanban repo**, cloned as a
sibling of AlfredHomeHub on the Pi:

```
~/projects/
  AlfredHomeHub/     # this repo — has docker/docker-compose.yml (kanban-api service)
  Kanban_App/        # the Kanban repo — has pi-server/ (Dockerfile + server) + operations.js
```

The compose build context is `../../Kanban_App` (relative to `docker/`). Override
with `KANBAN_REPO_PATH` if cloned elsewhere.

### First-time bring-up

```bash
# On the Pi: clone the Kanban repo beside AlfredHomeHub (once)
git clone git@github.com:Kezman554/Kanban_App.git ~/projects/Kanban_App

# On the LAPTOP: seed the Pi's DB copy (one-time; a copy, NOT a migration —
# the laptop app stays authoritative). Prints the snapshot counts to verify.
scripts/kanban-seed-db.sh

# On the Pi: build + start the service
cd ~/projects/AlfredHomeHub/docker && docker compose up -d --build kanban-api

# On the Pi: install the nightly backup + vault export crons (once)
~/projects/AlfredHomeHub/scripts/setup-kanban-backup.sh
~/projects/AlfredHomeHub/scripts/setup-kanban-export.sh
```

### Pull-and-restart loop (routine redeploy — one command)

Run on the Pi. Pulls both repos and rebuilds the container; the DB copy on the
bind-mounted volume survives untouched:

```bash
cd ~/projects/Kanban_App && git pull --ff-only && \
cd ~/projects/AlfredHomeHub && git pull --ff-only && \
docker compose -f docker/docker-compose.yml up -d --build kanban-api && \
scripts/kanban-smoke.sh
```

### Verify

```bash
scripts/kanban-smoke.sh                 # container up, API healthy, API card count == DB
curl -s http://192.168.1.100:8300/stats # {"projects":9,"cards":...,"done":107,...}
```

> **DB ownership:** the board DB on the Pi is a disposable copy. The laptop
> Electron app is authoritative until the UI-port card repoints the UI at this
> API. Don't hand-edit the Pi copy expecting it to flow back — it won't.
> Coordination note: once the Pi export cron is live, the laptop should stop
> pushing its own `kanban-export.json`/`kanban-summary.md` so the two don't
> both write `4-dev-hub/` (the Pi is the export owner now).
