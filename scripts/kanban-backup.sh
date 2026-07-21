#!/usr/bin/env bash
# Back up the Pi's Kanban DB copy so the board survives an SD/SSD failure.
# The board is the single source of truth for the projects; a nightly snapshot
# to a separate directory is the simplest thing that makes it recoverable.
#
# Prefers sqlite3's online .backup (consistent even if the API is mid-write);
# falls back to a plain copy when the sqlite3 CLI isn't installed (safe enough
# at the quiet hour this runs, single-writer board). Keeps the last KEEP copies.
set -euo pipefail

# --- Config -------------------------------------------------------------------
DB_PATH="${DB_PATH:-/home/kezman554/kanban-data/kanban.db}"
BACKUP_DIR="${BACKUP_DIR:-/home/kezman554/backups/kanban}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kanban-backup.log}"
KEEP="${KEEP:-14}"

# --- Logging ------------------------------------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")" "${BACKUP_DIR}"
log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"; }
fail() { log "ERROR: $*"; exit 1; }

# --- Preconditions ------------------------------------------------------------
[ -f "${DB_PATH}" ] || fail "no DB at ${DB_PATH}"

DEST="${BACKUP_DIR}/kanban-$(date '+%Y-%m-%d-%H%M').db"

# --- Backup -------------------------------------------------------------------
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${DB_PATH}" ".backup '${DEST}'" || fail "sqlite3 .backup failed"
    method="sqlite3 .backup"
else
    cp "${DB_PATH}" "${DEST}.tmp" && mv "${DEST}.tmp" "${DEST}" || fail "cp failed"
    method="cp"
fi

# Quick integrity check when sqlite3 is available — a corrupt snapshot is worse
# than none, because it hides the fact that there's no good backup.
if command -v sqlite3 >/dev/null 2>&1; then
    ok="$(sqlite3 "${DEST}" 'PRAGMA integrity_check;' 2>&1 || true)"
    [ "${ok}" = "ok" ] || fail "integrity check failed on ${DEST}: ${ok}"
fi

# --- Rotate -------------------------------------------------------------------
# Delete all but the newest KEEP snapshots.
mapfile -t old < <(ls -1t "${BACKUP_DIR}"/kanban-*.db 2>/dev/null | tail -n +"$((KEEP + 1))")
for f in "${old[@]}"; do rm -f "${f}"; done

log "OK: ${method} -> $(basename "${DEST}") ($(wc -c <"${DEST}") bytes); ${#old[@]} old pruned"
