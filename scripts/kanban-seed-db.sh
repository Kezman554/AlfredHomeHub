#!/usr/bin/env bash
# Seed the Pi's Kanban DB COPY from the laptop — a ONE-TIME bring-up step, not
# a migration. The laptop Electron app stays the authoritative board; this just
# hands the Pi API a copy to serve (see docs/pi-port-analysis.md). Run from the
# LAPTOP (it has the source DB and scp access to the Pi).
#
#   scripts/kanban-seed-db.sh            # seed, refuse to clobber an existing copy
#   scripts/kanban-seed-db.sh --force    # overwrite the copy already on the Pi
#
# It records the source project/card/done counts before copying and prints them
# so they can be checked against http://<pi>:8300/stats once the container is up
# — that is the migration-integrity check (snapshot at copy time, since the live
# board keeps changing).
set -euo pipefail

# --- Config (laptop-side paths) ----------------------------------------------
SRC_DB="${SRC_DB:-/c/Users/Nick/Desktop/KanbanBuild/data/kanban.db}"
PI_HOST="${PI_HOST:-kezman554@192.168.1.100}"
PI_DATA_DIR="${PI_DATA_DIR:-/home/kezman554/kanban-data}"
PI_DB="${PI_DATA_DIR}/kanban.db"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# --- Preconditions ------------------------------------------------------------
[ -f "${SRC_DB}" ] || { echo "ERROR: source DB not found: ${SRC_DB}" >&2; exit 1; }

# This half runs on the laptop, where `python3` may be a non-functional
# Windows Store alias that resolves on PATH but errors when run. Pick the first
# candidate that actually executes.
PY=""
for cand in python3 python py; do
    if command -v "${cand}" >/dev/null 2>&1 && "${cand}" --version >/dev/null 2>&1; then
        PY="${cand}"; break
    fi
done
[ -n "${PY}" ] || { echo "ERROR: no working python interpreter found" >&2; exit 1; }

# --- Snapshot source counts (the integrity baseline) --------------------------
counts="$("${PY}" -c '
import sqlite3, sys
db = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
q = lambda s: db.execute(s).fetchone()[0]
print(q("SELECT COUNT(*) FROM projects"),
      q("SELECT COUNT(*) FROM cards"),
      q("SELECT COUNT(*) FROM cards WHERE status=\"Done\""))
' "${SRC_DB}")"
read -r SRC_PROJECTS SRC_CARDS SRC_DONE <<<"${counts}"
echo "Source snapshot: ${SRC_PROJECTS} projects / ${SRC_CARDS} cards / ${SRC_DONE} done"

# --- Guard against clobbering an existing Pi copy -----------------------------
if [ "${FORCE}" -ne 1 ] && ssh "${PI_HOST}" "test -f '${PI_DB}'"; then
    echo "ERROR: ${PI_DB} already exists on the Pi. Re-run with --force to overwrite." >&2
    echo "       (The Pi copy is disposable — the laptop is authoritative — but overwriting" >&2
    echo "        is explicit so a live Pi copy is never lost by accident.)" >&2
    exit 1
fi

# --- Copy ---------------------------------------------------------------------
# Copy to a temp name on the Pi then move into place, so the API never opens a
# half-transferred file if it happens to be starting.
ssh "${PI_HOST}" "mkdir -p '${PI_DATA_DIR}'"
scp "${SRC_DB}" "${PI_HOST}:${PI_DB}.seed-tmp"
ssh "${PI_HOST}" "mv '${PI_DB}.seed-tmp' '${PI_DB}'"

echo
echo "Seeded ${PI_HOST}:${PI_DB}"
echo "Verify after the container is up:"
echo "  curl -s http://192.168.1.100:8300/stats"
echo "  expect: projects=${SRC_PROJECTS} cards=${SRC_CARDS} done=${SRC_DONE}"
