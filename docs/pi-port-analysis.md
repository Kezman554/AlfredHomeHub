# Kanban → Pi: port analysis

**Date:** 2026-07-20
**Status:** discovery only — nothing built, nothing deployed, nothing migrated.
**Trigger:** Card N ("Deploy Kanban App to Pi") task 1 — "confirm the runtime."

Task 1 did its job: it proved Card N is mis-scoped. This doc banks the
discovery so the finding survives, and proposes a two-card split. **Both cards
stay parked until after France** — GymSync has the rest of W30.

---

## The finding

**The Kanban app is an Electron desktop app with no HTTP server.** Card N
(tasks 2–8) assumes a web service. It isn't one, and two tasks are impossible
as written:

- **Task 5** ("fixed LAN address, open it in a browser tab") — Electron
  renders to a native Chromium window. Containerised on a headless Pi there is
  no display and nothing to serve.
- **Test "API serves live data — curl the endpoint"** — there is no endpoint.
  43 `ipcMain.handle` IPC handlers, **zero HTTP routes** (grepped `src/` and
  `scripts/` for express/fastify/createServer/`.listen(` — nothing).

Containerising the app as-is produces a headless container that serves
nothing. The real work behind Card N is an **Electron → web port**, which the
card never scoped.

### Runtime, as confirmed

- **Location:** `C:\Users\Nick\Desktop\KanbanBuild` (git repo,
  `github.com/Kezman554/Kanban_App.git`).
- **Started by:** `npm run dev` (Vite renderer on 8502) + `electron .` — opens
  a native desktop window. Not a service.
- **Database:** SQLite via `better-sqlite3`. Live file is
  `KanbanBuild/data/kanban.db` (~397 KB, modified during this session). The
  root-level `kanban.db` is a **0-byte decoy** — do not migrate that one.
- **Build step:** yes — Vite (`dist/`) plus `vite-plugin-electron`
  (`dist-electron/`).

---

## The port seam (why this is tractable, not a from-scratch rewrite)

- **`src/database/operations.js`** — 1432 lines, 28 methods, **zero Electron
  references** (its only `require` is `./schema`). This is a clean, portable
  data layer. The hard part is already done: an HTTP API can wrap it directly.
- **One chokepoint.** The renderer reaches all data through a single
  `contextBridge.exposeInMainWorld('electron', …)` in `src/main/preload.js`.
  Reimplementing that one file over `fetch` ports most of the **63** renderer
  call sites without touching the React components.
- **UI is already React + Vite**, building to static files (`dist/`) — servable
  as-is behind the API.

### Genuinely won't port

- **8 native-dialog call sites** — `selectDirectory` (4), `openInExplorer` (2),
  `openJsonFile` (2). Native OS dialogs; no browser equivalent. Must be
  replaced (e.g. text-path input, upload) or dropped.
- **`src/main/fileReader.js`** — reads PRDs/progress from laptop paths like
  `C:\Dev\…` that don't exist on the Pi. Path convention needs rethinking or
  the feature drops server-side.
- **Embedded terminal** — already dead (node-pty won't compile with VS Build
  Tools 2026, per the app's CLAUDE.md), so no loss.
- **`better-sqlite3`** — the installed binary is compiled against Electron's
  ABI (confirmed: `NODE_MODULE_VERSION 143` vs plain Node's 137). Needs an
  **ARM64 rebuild** for a Node/container runtime.

---

## Baseline (confirmed against the live DB, read-only)

| Metric   | Count |
|----------|-------|
| projects | 9     |
| cards    | 233   |
| done     | 107   |

Card statuses: `Done` 107, `Not Started` 117, `In Progress` 9.

> ⚠️ **Status values are capitalised** (`'Done'`, `'In Progress'`,
> `'Not Started'`). Any verification query using `status='done'` returns **0**.
> Use `status='Done'`.

Projects (id order): KitchenSync, LOTR YouTube Channel, Requiem NFT Launcher,
Portfolio CV Website, Obsidian Vault Setup, Personal Morning Alarm, Alfred Home
Hub, GymSync, DocuSync.

---

## Card N's open question — resolved favourably

Card N asked which way the vault sync runs, because a Pi-written
`kanban-export.json` could be clobbered by an older laptop copy (stale board
state at Claude Code session start — the failure least likely to be noticed).

- Export path is **hardcoded** in `src/main/main.js`:
  `C:\Dev\alfred-vault\4-dev-hub\kanban-export.json` (summary alongside it).
- The Pi's `scripts/vault-sync.sh` is **pull-only** — so a naive Pi-side write
  would indeed never reach the laptop.
- **But** the Vault API already commits-and-pushes vault writes, and vault
  commit `81f3ceb` (19 Jul) wired a **laptop-side session-start pull hook**. So
  a Pi-written export reaches the laptop via the existing push→pull pattern —
  no new sync machinery needed. The export step is viable Pi-side.

---

## Proposed split (recommendation only — board not touched)

**Card N (rescoped) — Kanban backend + deploy.**
HTTP API wrapping `operations.js`, containerise into the AlfredHomeHub compose
stack, persistent volume for the DB, backup, `DEPLOY.md` pull-and-restart loop,
Pi-side vault export. **Explicitly NOT migrating the live DB** — the laptop
stays authoritative until the UI can be repointed, so there is no split-brain
risk on 233 live cards. The Pi API runs against a **copy** for bring-up.

**New card — Kanban UI port.**
Rewire the `preload.js` bridge to `fetch`, serve the React build behind the
API, replace or drop the 8 native-dialog call sites, ARM64-rebuild
`better-sqlite3`. This is the card that delivers the browser-tab board and lets
the live DB become authoritative on the Pi.

Sequencing unchanged from the vault note: backend/deploy first, UI port second,
**then** the writable DailySync kanban tile (which is card N's actual driver —
a vault export can't take writes, so the tile needs a live API behind it).
