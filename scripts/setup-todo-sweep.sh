#!/usr/bin/env bash
# One-time setup for the nightly rolling to-do sweep on the Pi:
#   1. Install sweep-todo.sh as a cron job
#   2. Run one sweep to confirm the entry point works
#
# Idempotent: safe to re-run; replaces any existing sweep cron entry.
# Requires the vault API container to be up (the sweep goes through it).
set -euo pipefail

# --- Config -------------------------------------------------------------------
SWEEP_URL="${SWEEP_URL:-http://localhost:8200/chalkboard/sweep}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/todo-sweep.log}"
# 03:07 — a quiet overnight slot, deliberately off the */10 minute boundary so
# it can never start in the same minute as the vault-sync cron (they'd still
# serialise on the shared lock, but not even queueing is better).
CRON_SCHEDULE="7 3 * * *"

SWEEP_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sweep-todo.sh"

# --- 1. Install cron job ------------------------------------------------------
chmod +x "${SWEEP_SCRIPT}"

CRON_ENTRY="${CRON_SCHEDULE} SWEEP_URL=${SWEEP_URL} LOG_FILE=${LOG_FILE} ${SWEEP_SCRIPT}"

# Rebuild the crontab with any existing sweep line stripped out, then append
# ours. Re-running therefore replaces the entry rather than duplicating it.
{
    crontab -l 2>/dev/null | grep -Fv "${SWEEP_SCRIPT}" || true
    printf '%s\n' "${CRON_ENTRY}"
} | crontab -

# --- 2. Initial sweep ---------------------------------------------------------
echo "Running one sweep via the cron entry point..."
SWEEP_URL="${SWEEP_URL}" LOG_FILE="${LOG_FILE}" "${SWEEP_SCRIPT}"

# --- Confirmation -------------------------------------------------------------
echo
echo "To-do sweep installed."
echo "  Endpoint: ${SWEEP_URL}"
echo "  Log:      ${LOG_FILE}"
echo "  Cron:     $(crontab -l | grep -F "${SWEEP_SCRIPT}")"
echo "  Last log: $(tail -1 "${LOG_FILE}")"
