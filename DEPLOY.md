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
| 8200 | Vault API | Vault content + rolling to-do writes. `http://192.168.1.100:8200` — `/health`, `/chalkboard` (GET/POST, `/tick`, `/drop`, `/sweep`), `/daily-schedule`, `/daily-schedule/week`, `/shopping` (discovery GET/POST create, `/sweep` POST clear-bought, `/{list_id}` GET/POST add, `/tick`, `/drop`), `/inbox` (GET list, POST capture — body is `text/plain`), `/calendar/events` (GET — family calendar, read through Home Assistant) |
| 8300 | Kanban board + API | The **browser board** — bookmark **`http://192.168.1.100:8300`** on laptop and phone. The React UI is served as static files from the same container, over the board's data API (same origin, so no CORS). **The Pi DB is now authoritative** (migrated from the laptop); the Electron app is dev-only/read-only. API: `/health`, `/stats`, `/projects`, `/projects/{id}` (+ `/workable`, `/done`, `/next-letter`, `POST /append`), `/cards/{id}` (+ `/status`, `/details`, `/notes`, `/prompt`, `/clear-dependencies`, `DELETE`), `/export/json`, `/export/summary`, `/export/all-projects` |
| 8400 | KitchenSync | Recipes, meal plan, pantry, shopping — **PWA + API in one container**, same origin. Bookmark **`http://192.168.1.100:8400`** on laptop and phone. API: `/health`, `/docs`, `/api/recipes`, `/api/meals` (+ `/today`, `/{id}/cook`), `/api/pantry`, `/api/shopping-list` (+ `/generate`, `/items`), `/api/images` (POST upload), `/api/media/*`, `/api/export/run` (vault snapshot) |

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

## Family calendar (vault-api → Home Assistant)

`GET http://192.168.1.100:8200/calendar/events` returns the shared Google
Calendar ("Young Family"). **Read-only** — creating and updating events is a
later card.

The calendar is read **through Home Assistant**, not from Google directly: HA
holds the Google OAuth credentials and refreshes them, so vault-api needs no
Google auth of its own and has no token of its own to keep alive.

```
vault-api :8200  ──Bearer token──▶  Home Assistant :8123  ──OAuth──▶  Google Calendar
```

### Configuration

Set in `docker/docker-compose.yml`, with the secret supplied from
`docker/.env` (**gitignored — never commit the token**):

| Variable | Default | Notes |
|---|---|---|
| `HA_BASE_URL` | `http://192.168.1.100:8123` | HA runs `network_mode: host`, so this is the Pi's **LAN address** — a compose service name would not resolve. |
| `HA_TOKEN` | *(none)* | **Required.** Long-lived access token. |
| `HA_CALENDAR_ENTITY` | `calendar.young_family` | Configurable so a second calendar needs no code change. |
| `HA_TIMEOUT` | `10` | Seconds. Short: this sits behind a dashboard tile. |
| `CALENDAR_DEFAULT_DAYS` | `7` | How far ahead a bare request looks. |

Mint the token in HA: **profile → Security → Long-lived access tokens →
Create token**. Then:

```bash
# on the Pi
printf 'HA_TOKEN=%s\n' '<paste-token>' >> ~/projects/AlfredHomeHub/docker/.env
chmod 600 ~/projects/AlfredHomeHub/docker/.env
cd ~/projects/AlfredHomeHub/docker && docker compose up -d --build vault-api
```

`HA_TOKEN` deliberately defaults to **empty** rather than being marked required
in compose. Compose parses the whole file on any `up`, so a `:?` guard would
block the entire stack — Home Assistant and KitchenSync included — over a
missing calendar token. Instead only `/calendar/events` fails, with a 500
naming the variable.

### Response shape

```json
{
  "calendar": "young_family",
  "events": [
    {"summary": "France", "start": "2026-07-24", "end": "2026-07-25",
     "all_day": true, "location": null, "description": null}
  ]
}
```

HA distinguishes all-day from timed events **structurally** — `{"date": …}` vs
`{"dateTime": …}` — and that is what sets `all_day`. All-day `end` is passed
through **exclusive**, exactly as Google and HA give it: a single day on the
24th ends `2026-07-25`. Adjusting it would make single-day and multi-day events
disagree about what `end` means.

Events are sorted by start, with a day's all-day events ahead of its timed ones.
A malformed event is skipped rather than failing the request — one bad entry on
a shared calendar must not blank the dashboard panel.

### Errors

| Condition | Status |
|---|---|
| `HA_TOKEN` unset | **500**, naming the variable |
| HA unreachable, or non-200 (e.g. an expired token → 401) | **502**, with the upstream status |
| `end` before `start` | **422** (HA would answer `[]`, which reads as "nothing on") |
| Unparseable date | **422** |

The token never appears in an error body or a log line.

### Verify

```bash
curl -s "http://192.168.1.100:8200/calendar/events" | python3 -m json.tool
curl -s "http://192.168.1.100:8200/calendar/events?start=2026-07-24&end=2026-07-26"
```

## KitchenSync (two-repo build)

Recipes, meal planning, pantry and shopping list — one container serving both
the React PWA and the API from the same origin (so no CORS). Like the Kanban
API it lives in a **separate repo** (`git@github.com:Kezman554/KitchenSync.git`,
**private**) and is defined here but **built from there**, cloned as a sibling:

```
~/projects/
  AlfredHomeHub/     # this repo — has docker/docker-compose.yml (kitchensync service)
  Kanban_App/
  KitchenSync/       # the KitchenSync repo — Dockerfile at its root
```

Build context is `../../KitchenSync` (relative to `docker/`); override with
`KITCHENSYNC_REPO_PATH`.

> **Private repo — clone via the `github-user` SSH alias.** The Pi's
> `~/.ssh/config` pins `github.com` to the **ObsidianVault deploy key**, which
> cannot see other repos. A `github-user` alias (added to that file, pointing at
> `~/.ssh/id_ed25519`, the full-access user key) exists for repos the deploy key
> can't reach. The `github.com` entry is deliberately untouched — the containers'
> vault pushes depend on it.

### First-time bring-up

```bash
# On the Pi: clone KitchenSync beside AlfredHomeHub (once) — note the alias
git clone git@github-user:Kezman554/KitchenSync.git ~/projects/KitchenSync

# On the Pi: build + start
cd ~/projects/AlfredHomeHub/docker && docker compose up -d --build kitchensync

# On the Pi: install the nightly snapshot + backup crons (once).
# The backup script also runs a restore DRILL as its last step — setup is not
# finished until a restore has been proved.
~/projects/AlfredHomeHub/scripts/setup-kitchensync-export.sh
~/projects/AlfredHomeHub/scripts/setup-kitchensync-backup.sh
```

### Pull-and-restart loop (routine redeploy)

```bash
cd ~/projects/KitchenSync && git pull --ff-only && \
cd ~/projects/AlfredHomeHub && git pull --ff-only && \
docker compose -f docker/docker-compose.yml up -d --build kitchensync
```

`AUTO_MIGRATE=true`, so pending Alembic migrations apply on boot — there is no
separate migrate step. **Take a backup immediately before any deploy that
carries a migration** (`scripts/kitchensync-backup.sh`).

### Data — what must survive

Both live under `/data` on the **named volume `kitchensync-data`**, and neither
is in git:

| Path | Holds |
|---|---|
| `/data/kitchensync.db` | recipes, meal plans, pantry, shopping list, activity history |
| `/data/media/` | uploaded recipe images |

`docker compose down`, a rebuild and an image change all leave the volume alone.
**`docker compose down -v` destroys it** — that flag deletes household data.

The volume is pinned to the literal name `kitchensync-data` (via `name:` in the
compose `volumes:` block) rather than the compose-project-prefixed
`docker_kitchensync-data`, because the backup script mounts it by name from
outside compose.

### Backup + restore

```bash
scripts/kitchensync-backup.sh              # stop app, tar volume, verify, rotate, restart
scripts/restore-kitchensync.sh --drill     # restore newest archive to a SCRATCH volume + verify
scripts/restore-kitchensync.sh --live ARCHIVE   # overwrite live data (prompts for RESTORE)
```

The backup briefly stops the container so the SQLite copy is consistent (a live
tar can catch a torn write plus a stale `-wal`); the restart is in a trap, so an
interrupted run still brings the app back. It verifies the archive is readable
and contains the DB *before* rotating older ones away.

The drill restores into a throwaway volume and checks, using the **app image**
(same Python, SQLite and Alembic as production): the DB opens, passes
`PRAGMA integrity_check`, sits at the image's Alembic **head**, holds rows in all
four modules, and that **every image the DB references resolves in `media/`**.

**Offsite (restic → B2) is LIVE as of 2026-07-23** — repo
`b2:alfred-pi-backup:kitchensync`, restic 0.18.0, running nightly at 03:47 and
**drilled end-to-end from B2** (see below). Credentials live in
`/home/kezman554/.config/kitchensync-restic.env` (chmod 600, **never** in git):

```bash
export RESTIC_REPOSITORY=b2:<bucket>:kitchensync
export RESTIC_PASSWORD=<repo encryption key — lose this and the backup is unreadable>
export B2_ACCOUNT_ID=<key id>          # B2 key ID
export B2_ACCOUNT_KEY=<application key>
```

Native B2 backend, so it is `B2_ACCOUNT_ID` / `B2_ACCOUNT_KEY` — the `AWS_*`
pair applies only to an S3-style `s3:` repository URL.

`export` matters: the cron sources this file and then runs the backup script as
a **child process**, and a plain `VAR=value` line is a shell variable that a
child never inherits. (The cron entry wraps the source in `set -a`, so an env
file without `export` also works — but write them with `export` and it is
correct under either.) Get this wrong and the offsite leg fails every night
while the local leg keeps succeeding, which is easy not to notice.

Optional retention, with the defaults the script uses:
`RESTIC_KEEP_DAILY=7`, `RESTIC_KEEP_WEEKLY=4`, `RESTIC_KEEP_MONTHLY=6`.

Then:

```bash
chmod 600 /home/kezman554/.config/kitchensync-restic.env
sudo apt install restic
~/projects/AlfredHomeHub/scripts/setup-kitchensync-backup.sh   # detects the file
```

Setup prints `Offsite: will source …` when it finds the file. **Verify the leg
actually ran** — do not trust the absence of an error:

```bash
tail -1 /home/kezman554/logs/kitchensync-backup.log
#   want: offsite: restic OK -> b2:<bucket>:kitchensync
#   not:  offsite: skipped (RESTIC_REPOSITORY unset)   <- file not picked up
#   not:  offsite: RESTIC FAILED                       <- credentials/bucket wrong

set -a; . /home/kezman554/.config/kitchensync-restic.env; set +a
restic snapshots        # the archives really in B2
```

### The B2 restore drill — done 2026-07-23, repeat before trusting it again

Proving a restore **from B2** is the only thing that tests the copy that matters
once the Pi is gone. A local-archive drill does not: it passes even if nothing
was ever uploaded.

Move the local archives aside first, or the drill can quietly satisfy itself
from disk and tell you nothing:

```bash
set -a; . /home/kezman554/.config/kitchensync-restic.env; set +a

mv ~/backups/kitchensync ~/backups/kitchensync-HIDDEN     # force a real B2 fetch
restic restore latest --target /tmp/b2-drill
scripts/restore-kitchensync.sh --drill \
  /tmp/b2-drill/home/kezman554/backups/kitchensync/kitchensync-*.tar.gz

mv ~/backups/kitchensync-HIDDEN ~/backups/kitchensync     # put them back
rm -rf /tmp/b2-drill && docker volume rm kitchensync-restore-drill
```

**Result on 2026-07-23:** with a recipe + image, a meal, a pantry item and a
shopping item in the DB — backed up, uploaded, local copies hidden, pulled back
from B2 and verified: `integrity_check` ok, schema at head `8dfc8c93402c`, rows
present in all four modules, the referenced image resolved. `RESTORE VERIFIED`,
exit 0. Test data was purged afterwards; the household DB is empty.

Worth repeating whenever the schema changes materially, and after any change to
the backup scripts.

> **`RESTIC_PASSWORD` is not recoverable.** It encrypts the repo; Backblaze
> cannot reset it and neither can you. Lose it and every offsite snapshot is
> permanently unreadable, however intact the bytes in B2 are. Store it
> somewhere that survives the Pi — a password manager, not just this file.

### Vault snapshot

`POST /api/export/run` writes `4-dev-hub/kitchensync-snapshot.json` and
`kitchensync-summary.md` into the vault, commits as Alfred and pushes — the same
single-writer pattern as the kanban export, under the shared vault write lock
(`.git/alfred-write.lock`), so it serialises with vault-sync and the other
exporters. A readable mirror, **not** a backup: no recipes, nothing restorable.

It **commits only when the content changed** — the snapshot carries no
generated-at timestamp, so an unchanged household adds no commit. A quiet log
reading `no-op: vault already up to date` is the normal, correct result.

### Nightly cron slots

All sit 7 minutes past a `*/10` boundary, clear of vault-sync:

| Time | Job |
|---|---|
| `*/10` | vault-sync |
| 03:07 | to-do sweep |
| 03:17 | kanban export |
| 03:27 | kanban backup |
| 03:37 | **KitchenSync vault snapshot** (`kitchensync-export.sh`) |
| 03:47 | **KitchenSync backup** (`kitchensync-backup.sh`) — after the snapshot, since it briefly stops the container |

### Verify

```bash
curl -s http://192.168.1.100:8400/health        # {"status":"ok","version":"0.1.0"}
curl -s http://192.168.1.100:8400/api/recipes   # data
curl -I http://192.168.1.100:8400/planner       # 200 text/html — SPA deep link
```

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
