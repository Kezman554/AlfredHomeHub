#!/usr/bin/env bash
# Back up KitchenSync's household data — the SQLite DB and the uploaded recipe
# images, which both live on the `kitchensync-data` volume.
#
# NEITHER IS IN GIT. Recipes typed up, months of meal history and uploaded
# photos cannot be retyped from memory, and AUTO_MIGRATE=true means every
# deploy may apply a schema migration over them. This is the safety net for
# both.
#
# The container is STOPPED for the copy and started again after. That is a few
# seconds of downtime at 03:47 in exchange for a guaranteed-consistent archive:
# tarring a live SQLite file can capture a torn write plus a stale -wal. The
# stop is in a trap, so an interrupted run still brings the app back up.
#
# Two legs:
#   1. LOCAL  — always. A rotated tar.gz on the Pi. Survives a bad migration or
#      a fat-fingered delete; does NOT survive the Pi's disk dying.
#   2. OFFSITE — restic to Backblaze B2, only when RESTIC_REPOSITORY and its
#      credentials are set in the environment. That is the leg that survives
#      fire, theft and disk failure. Skipped with a log line when unconfigured,
#      so this script is useful before the B2 account exists.
#
# Restore: see restore-kitchensync.sh, and DEPLOY.md's restore drill. An
# untested backup is a belief, not a safety net.
set -euo pipefail

# --- Config -------------------------------------------------------------------
VOLUME="${VOLUME:-kitchensync-data}"
CONTAINER="${CONTAINER:-kitchensync}"
COMPOSE_FILE="${COMPOSE_FILE:-/home/kezman554/projects/AlfredHomeHub/docker/docker-compose.yml}"
BACKUP_DIR="${BACKUP_DIR:-/home/kezman554/backups/kitchensync}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kitchensync-backup.log}"
KEEP="${KEEP:-14}"

# Offsite leg. Set these (in a root-owned env file sourced by cron, never in
# git) to enable it:
#   RESTIC_REPOSITORY=b2:alfred-kitchensync:kitchensync
#   RESTIC_PASSWORD=...            # the repo encryption key — lose it, lose the backup
#   B2_ACCOUNT_ID=...  B2_ACCOUNT_KEY=...
RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-}"
RESTIC_KEEP_DAILY="${RESTIC_KEEP_DAILY:-7}"
RESTIC_KEEP_WEEKLY="${RESTIC_KEEP_WEEKLY:-4}"
RESTIC_KEEP_MONTHLY="${RESTIC_KEEP_MONTHLY:-6}"

# --- Logging ------------------------------------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")" "${BACKUP_DIR}"
log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"; }
fail() { log "ERROR: $*"; exit 1; }

# --- Preconditions ------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "docker not on PATH"
docker volume inspect "${VOLUME}" >/dev/null 2>&1 \
    || fail "no volume ${VOLUME} — is the stack up? (a project-prefixed name means compose renamed it)"

STAMP="$(date '+%Y-%m-%d-%H%M')"
DEST="${BACKUP_DIR}/kitchensync-${STAMP}.tar.gz"

# --- Stop the app, and guarantee it comes back --------------------------------
# The trap covers every exit path, including `set -e` aborts and Ctrl-C: the
# household's app must never be left down because a backup failed.
restart_app() {
    docker compose -f "${COMPOSE_FILE}" start "${CONTAINER}" >/dev/null 2>&1 \
        || log "WARNING: could not restart ${CONTAINER} — check it by hand"
}
trap restart_app EXIT

docker compose -f "${COMPOSE_FILE}" stop "${CONTAINER}" >/dev/null 2>&1 \
    || log "WARNING: stop failed (already down?) — continuing"

# --- 1. Local archive ---------------------------------------------------------
# busybox mounts the volume directly, so this works whether the volume is a
# named volume or a bind mount, and needs nothing installed on the Pi.
docker run --rm \
    -v "${VOLUME}":/data:ro \
    -v "${BACKUP_DIR}":/backup \
    busybox tar czf "/backup/$(basename "${DEST}").tmp" -C /data . \
    || fail "tar of ${VOLUME} failed"

mv "${DEST}.tmp" "${DEST}"

SIZE="$(wc -c <"${DEST}")"
# An archive that small holds no database — treat it as a failure rather than
# rotating a good backup out in favour of an empty one.
[ "${SIZE}" -gt 1024 ] || fail "archive suspiciously small (${SIZE} bytes) — backing up nothing?"

# Prove the tar is readable and actually contains the DB, before it is trusted
# enough to rotate older archives away.
docker run --rm -v "${BACKUP_DIR}":/backup:ro busybox \
    tar tzf "/backup/$(basename "${DEST}")" >/dev/null 2>&1 \
    || fail "archive ${DEST} is unreadable"
docker run --rm -v "${BACKUP_DIR}":/backup:ro busybox \
    sh -c "tar tzf '/backup/$(basename "${DEST}")' | grep -q kitchensync.db" \
    || fail "archive ${DEST} contains no kitchensync.db"

# --- 2. Offsite (restic → B2) -------------------------------------------------
offsite="skipped (RESTIC_REPOSITORY unset)"
if [ -n "${RESTIC_REPOSITORY}" ]; then
    if ! command -v restic >/dev/null 2>&1; then
        offsite="SKIPPED — restic not installed"
        log "WARNING: RESTIC_REPOSITORY set but restic is not installed"
    else
        export RESTIC_REPOSITORY
        # First run initialises the repo; afterwards `init` fails harmlessly.
        restic snapshots >/dev/null 2>&1 || restic init >/dev/null 2>&1 || true

        if restic backup "${DEST}" --tag kitchensync --quiet >/dev/null 2>&1; then
            restic forget --tag kitchensync \
                --keep-daily "${RESTIC_KEEP_DAILY}" \
                --keep-weekly "${RESTIC_KEEP_WEEKLY}" \
                --keep-monthly "${RESTIC_KEEP_MONTHLY}" \
                --prune --quiet >/dev/null 2>&1 \
                || log "WARNING: restic forget/prune failed — repo will grow"
            offsite="restic OK -> ${RESTIC_REPOSITORY}"
        else
            # Local leg already succeeded, so this is a warning, not a failure:
            # better a local-only backup than none. It is still logged loudly.
            offsite="RESTIC FAILED"
            log "WARNING: restic backup failed — offsite copy is NOT current"
        fi
    fi
fi

# --- Rotate local -------------------------------------------------------------
mapfile -t old < <(ls -1t "${BACKUP_DIR}"/kitchensync-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))")
for f in "${old[@]}"; do rm -f "${f}"; done

log "OK: $(basename "${DEST}") (${SIZE} bytes); ${#old[@]} old pruned; offsite: ${offsite}"
