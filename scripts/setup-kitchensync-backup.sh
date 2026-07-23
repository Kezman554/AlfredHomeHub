#!/usr/bin/env bash
# One-time setup for the KitchenSync backup:
#   1. Install kitchensync-backup.sh as a cron job
#   2. Run one backup to confirm it works
#   3. Immediately run a restore DRILL against that archive
#
# Step 3 is the point. A backup that has never been restored is a belief, not a
# safety net, so setup is not finished until a restore has been proved once.
#
# Idempotent: safe to re-run; replaces any existing backup cron entry.
set -euo pipefail

# --- Config -------------------------------------------------------------------
BACKUP_DIR="${BACKUP_DIR:-/home/kezman554/backups/kitchensync}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kitchensync-backup.log}"
KEEP="${KEEP:-14}"
# Nightly at 03:47 — the next free slot after the KitchenSync snapshot (03:37),
# off the */10 vault-sync boundary. This one briefly stops the container, so it
# deliberately runs AFTER the snapshot export rather than before it.
CRON_SCHEDULE="${CRON_SCHEDULE:-47 3 * * *}"

# Optional offsite leg. If you keep restic/B2 credentials in an env file, point
# RESTIC_ENV_FILE at it and the cron entry will source it. The file must NOT be
# in git — chmod 600, owned by the Pi user.
RESTIC_ENV_FILE="${RESTIC_ENV_FILE:-/home/kezman554/.config/kitchensync-restic.env}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_SCRIPT="${SCRIPT_DIR}/kitchensync-backup.sh"
RESTORE_SCRIPT="${SCRIPT_DIR}/restore-kitchensync.sh"

chmod +x "${BACKUP_SCRIPT}" "${RESTORE_SCRIPT}"

# --- 1. Install cron job ------------------------------------------------------
if [ -f "${RESTIC_ENV_FILE}" ]; then
    # bash -c so the env file is sourced before the script runs; the offsite leg
    # switches itself on when it sees RESTIC_REPOSITORY.
    #
    # `set -a` around the source is what makes this work. The backup script is a
    # CHILD process, and plain `RESTIC_PASSWORD=...` lines in the env file create
    # shell variables, which children do not inherit — restic would run with no
    # password and fail every night while the local leg kept succeeding. set -a
    # marks everything sourced for export, so the env file works whether or not
    # its lines say `export`.
    CRON_CMD="/bin/bash -c 'set -a; . ${RESTIC_ENV_FILE}; set +a; BACKUP_DIR=${BACKUP_DIR} LOG_FILE=${LOG_FILE} KEEP=${KEEP} ${BACKUP_SCRIPT}'"
    echo "Offsite: will source ${RESTIC_ENV_FILE}"
else
    CRON_CMD="BACKUP_DIR=${BACKUP_DIR} LOG_FILE=${LOG_FILE} KEEP=${KEEP} ${BACKUP_SCRIPT}"
    echo "Offsite: not configured (${RESTIC_ENV_FILE} absent) — local backups only."
fi

CRON_ENTRY="${CRON_SCHEDULE} ${CRON_CMD}"
{
    crontab -l 2>/dev/null | grep -Fv "${BACKUP_SCRIPT}" || true
    printf '%s\n' "${CRON_ENTRY}"
} | crontab -

# --- 2. Initial backup --------------------------------------------------------
# Source the env file here too, exactly as the cron entry does. Without this the
# setup run exercises only the LOCAL leg and reports success while the offsite
# leg it just configured is skipped — the one run most likely to catch a wrong
# bucket or a bad key would be the one that never tries.
echo
echo "Running one backup..."
if [ -f "${RESTIC_ENV_FILE}" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${RESTIC_ENV_FILE}"
    set +a
fi
BACKUP_DIR="${BACKUP_DIR}" LOG_FILE="${LOG_FILE}" KEEP="${KEEP}" "${BACKUP_SCRIPT}"
echo "  $(tail -1 "${LOG_FILE}")"

# The offsite leg is reported inside that log line. Fail loudly here rather than
# printing a cheerful summary over a backup that never left the Pi.
if [ -f "${RESTIC_ENV_FILE}" ] && ! tail -1 "${LOG_FILE}" | grep -q "offsite: restic OK"; then
    echo
    echo "ERROR: credentials are configured but the offsite leg did not succeed:" >&2
    echo "  $(tail -1 "${LOG_FILE}")" >&2
    echo "Check the bucket name, the B2 key, and that 'restic snapshots' works." >&2
    exit 1
fi

# --- 3. Restore drill ---------------------------------------------------------
echo
echo "Running a restore drill against that archive..."
BACKUP_DIR="${BACKUP_DIR}" "${RESTORE_SCRIPT}" --drill

# --- Confirmation -------------------------------------------------------------
echo
echo "KitchenSync backup installed AND its restore proved."
echo "  Archives: ${BACKUP_DIR} (keep ${KEEP})"
echo "  Log:      ${LOG_FILE}"
echo "  Cron:     $(crontab -l | grep -F "${BACKUP_SCRIPT}")"
