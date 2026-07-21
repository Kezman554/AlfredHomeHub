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
| 8200 | Vault API | Vault content + rolling to-do writes. `http://192.168.1.100:8200` — `/health`, `/chalkboard` (GET/POST, `/tick`, `/drop`, `/sweep`), `/daily-schedule`, `/daily-schedule/week`, `/shopping` (discovery GET/POST create, `/sweep` POST clear-bought, `/{list_id}` GET/POST add, `/tick`, `/drop`), `/inbox` (GET list, POST capture — body is `text/plain`) |
| 8300 | Kanban board + API | The **browser board** — bookmark **`http://192.168.1.100:8300`** on laptop and phone. The React UI is served as static files from the same container, over the board's data API (same origin, so no CORS). **The Pi DB is now authoritative** (migrated from the laptop); the Electron app is dev-only/read-only. API: `/health`, `/stats`, `/projects`, `/projects/{id}` (+ `/workable`, `/done`, `/next-letter`, `POST /append`), `/cards/{id}` (+ `/status`, `/details`, `/notes`, `/prompt`, `/clear-dependencies`, `DELETE`), `/export/json`, `/export/summary`, `/export/all-projects` |

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

> **DB ownership (post UI-port):** the Pi DB is now **authoritative**. The
> browser board at `192.168.1.100:8300` is the single write surface — use it on
> laptop and phone. The **Electron app (`Desktop\KanbanBuild`) is dev-only /
> read-only**: do not tick, add, or edit cards there, or it diverges from the
> Pi. The laptop DB copy stays intact as a rollback.
>
> **Migration / re-seed:** the authoritative Pi DB was copied fresh from the
> live laptop DB via `scripts/kanban-seed-db.sh --force` (non-destructive — the
> laptop file is untouched). To re-seed again, stop the container first
> (`docker compose stop kanban-api`), run the seed with `--force`, then
> `up -d --build`.
>
> **Vault export — single writer:** the **Pi** is the only writer of
> `4-dev-hub/kanban-export.json` + `kanban-summary.md`. Both the nightly cron
> (`scripts/kanban-export.sh`) and the browser board's "Sync to Vault" button
> now go through **one path** — `POST /export/run` on the Kanban API, which
> regenerates the files, commits as Alfred and pushes, under the shared vault
> write lock (`.git/alfred-write.lock`, serialised with vault-sync + the vault
> API). The container holds a vault (rw) + `~/.ssh` (ro) mount for this, exactly
> like vault-api. The browser only *triggers* the export — it never writes the
> vault itself, so the Pi stays the single writer. The Electron app's "sync to
> vault" must still not be used.

## Browser board (UI-port)

The Kanban board opens in a browser tab on laptop or phone at
`http://192.168.1.100:8300`. It is the same React UI as the Electron app, with
the Electron `preload.js` bridge reimplemented over `fetch` to the local API
(`src/renderer/electron-web-shim.js` in the Kanban repo) and served as static
files by the Pi container. Redeploy is the same pull-and-restart loop above —
the multi-stage image rebuilds the UI.

**Feature gaps vs the desktop app** (browser can't do these; no dead buttons —
each degrades with a visible message):

| Feature | Browser behaviour |
|---|---|
| Import JSON (open file) | ✅ Works — file picker upload |
| Export project(s) to JSON (save file) | ✅ Works — browser download |
| Set project directory | ⚠️ Manual path entry (no native folder picker) |
| Open project folder in Explorer | ❌ Removed (desktop-only) |
| Read PRD / progress / CLAUDE.md into prompts | ❌ Removed (files live on the laptop) |
| AI prompt generation + API-key settings | ❌ Removed (never ship a key to a browser) |
| "Sync to vault" button | ✅ Works — triggers `POST /export/run` on the Pi (the Pi commits + pushes; the browser only triggers, so the Pi stays the single writer). Shows in-progress → "Exported N cards" / a visible error |
| Clear-all-data | ❌ Disabled (too destructive for the shared board) |
| Write-prompt-to-temp / launch Claude Code | ❌ Removed (copy-to-clipboard still works) |
| Embedded terminal | ❌ Off (already dead pre-port — node-pty) |

Anything in the ❌ rows is what the **dev-only Electron app** is still for.
