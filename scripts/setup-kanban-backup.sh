#!/usr/bin/env bash
# One-time setup for the nightly Kanban DB backup on the Pi:
#   1. Install kanban-backup.sh as a cron job
#   2. Run one backup to confirm the entry point works
#
# Idempotent: safe to re-run; replaces any existing backup cron entry.
set -euo pipefail

# --- Config -------------------------------------------------------------------
DB_PATH="${DB_PATH:-/home/kezman554/kanban-data/kanban.db}"
BACKUP_DIR="${BACKUP_DIR:-/home/kezman554/backups/kanban}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kanban-backup.log}"
# 03:27 — quiet, and staggered off vault-sync (*/10), the to-do sweep (03:07)
# and the kanban export (03:17).
CRON_SCHEDULE="${CRON_SCHEDULE:-27 3 * * *}"

BACKUP_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/kanban-backup.sh"

# --- 1. Install cron job ------------------------------------------------------
chmod +x "${BACKUP_SCRIPT}"

CRON_ENTRY="${CRON_SCHEDULE} DB_PATH=${DB_PATH} BACKUP_DIR=${BACKUP_DIR} LOG_FILE=${LOG_FILE} ${BACKUP_SCRIPT}"
{
    crontab -l 2>/dev/null | grep -Fv "${BACKUP_SCRIPT}" || true
    printf '%s\n' "${CRON_ENTRY}"
} | crontab -

# --- 2. Initial backup --------------------------------------------------------
echo "Running one backup via the cron entry point..."
DB_PATH="${DB_PATH}" BACKUP_DIR="${BACKUP_DIR}" LOG_FILE="${LOG_FILE}" "${BACKUP_SCRIPT}"

# --- Confirmation -------------------------------------------------------------
echo
echo "Kanban DB backup installed."
echo "  DB:       ${DB_PATH}"
echo "  Backups:  ${BACKUP_DIR}"
echo "  Log:      ${LOG_FILE}"
echo "  Cron:     $(crontab -l | grep -F "${BACKUP_SCRIPT}")"
echo "  Last log: $(tail -1 "${LOG_FILE}")"
