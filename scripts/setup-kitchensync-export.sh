#!/usr/bin/env bash
# One-time setup for the Pi-side KitchenSync vault snapshot:
#   1. Install kitchensync-export.sh as a cron job
#   2. Run one snapshot to confirm the entry point works
#
# Idempotent: safe to re-run; replaces any existing snapshot cron entry.
# Requires the kitchensync container up (the snapshot runs through its endpoint)
# and VAULT_DIR wired in compose, or the endpoint answers 503.
set -euo pipefail

# --- Config -------------------------------------------------------------------
EXPORT_URL="${EXPORT_URL:-http://localhost:8400/api/export/run}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kitchensync-export.log}"
# Nightly at 03:37 — the next free slot in the stack's sequence, off the */10
# vault-sync boundary and clear of the 03:07 sweep, 03:17 kanban export and
# 03:27 kanban backup, so the vault writers never queue on the shared lock.
CRON_SCHEDULE="${CRON_SCHEDULE:-37 3 * * *}"

EXPORT_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kitchensync-export.sh"

# --- 1. Install cron job ------------------------------------------------------
chmod +x "${EXPORT_SCRIPT}"

CRON_ENTRY="${CRON_SCHEDULE} EXPORT_URL=${EXPORT_URL} LOG_FILE=${LOG_FILE} ${EXPORT_SCRIPT}"
{
    crontab -l 2>/dev/null | grep -Fv "${EXPORT_SCRIPT}" || true
    printf '%s\n' "${CRON_ENTRY}"
} | crontab -

# --- 2. Initial snapshot ------------------------------------------------------
echo "Running one snapshot via the cron entry point..."
EXPORT_URL="${EXPORT_URL}" LOG_FILE="${LOG_FILE}" "${EXPORT_SCRIPT}"

# --- Confirmation -------------------------------------------------------------
echo
echo "KitchenSync vault snapshot installed."
echo "  Endpoint: ${EXPORT_URL}"
echo "  Log:      ${LOG_FILE}"
echo "  Cron:     $(crontab -l | grep -F "${EXPORT_SCRIPT}")"
echo "  Last log: $(tail -1 "${LOG_FILE}")"
