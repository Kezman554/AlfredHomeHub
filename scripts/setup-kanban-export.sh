#!/usr/bin/env bash
# One-time setup for the Pi-side Kanban vault export:
#   1. Install kanban-export.sh as a cron job
#   2. Run one export to confirm the entry point works
#
# Idempotent: safe to re-run; replaces any existing export cron entry.
# Requires the kanban-api container up (the export runs through its endpoint).
set -euo pipefail

# --- Config -------------------------------------------------------------------
EXPORT_URL="${EXPORT_URL:-http://localhost:8300/export/run}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kanban-export.log}"
# Nightly at 03:17 — a quiet slot, off the */10 vault-sync boundary and clear of
# the 03:07 to-do sweep so the two don't queue on the shared lock.
CRON_SCHEDULE="${CRON_SCHEDULE:-17 3 * * *}"

EXPORT_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kanban-export.sh"

# --- 1. Install cron job ------------------------------------------------------
chmod +x "${EXPORT_SCRIPT}"

CRON_ENTRY="${CRON_SCHEDULE} EXPORT_URL=${EXPORT_URL} LOG_FILE=${LOG_FILE} ${EXPORT_SCRIPT}"
{
    crontab -l 2>/dev/null | grep -Fv "${EXPORT_SCRIPT}" || true
    printf '%s\n' "${CRON_ENTRY}"
} | crontab -

# --- 2. Initial export --------------------------------------------------------
echo "Running one export via the cron entry point..."
EXPORT_URL="${EXPORT_URL}" LOG_FILE="${LOG_FILE}" "${EXPORT_SCRIPT}"

# --- Confirmation -------------------------------------------------------------
echo
echo "Kanban vault export installed."
echo "  Endpoint: ${EXPORT_URL}"
echo "  Log:      ${LOG_FILE}"
echo "  Cron:     $(crontab -l | grep -F "${EXPORT_SCRIPT}")"
echo "  Last log: $(tail -1 "${LOG_FILE}")"
