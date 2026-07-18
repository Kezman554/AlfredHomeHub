#!/usr/bin/env bash
# Pull the latest Obsidian vault onto the Pi and log the result.
# Intended to run unattended from cron (see setup-vault-sync.sh).
#
# Authentication is via an SSH deploy key already configured for the Pi user,
# so git runs non-interactively. This script never touches git credentials:
# if authentication fails, it logs the error and exits non-zero.
set -euo pipefail

# --- Config -------------------------------------------------------------------
VAULT_PATH="${VAULT_PATH:-/home/kezman554/alfred-vault}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/vault-sync.log}"

# --- Logging ------------------------------------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"
}

fail() {
    log "ERROR: $*"
    exit 1
}

# --- Preconditions ------------------------------------------------------------
[ -d "${VAULT_PATH}/.git" ] || fail "no git repo at ${VAULT_PATH} — run setup-vault-sync.sh first"

# --- Lock ---------------------------------------------------------------------
# Shared with the vault API's write path (WRITE_LOCK in src/vault_api/vault.py):
# a write transaction (pull-edit-commit-push) and this sync must serialise, or
# the pull can land mid-edit. Under .git/ so it never appears in the work tree.
# The lock is held for the rest of the script and released on exit.
LOCK_FILE="${VAULT_PATH}/.git/alfred-write.lock"
LOCK_TIMEOUT="${LOCK_TIMEOUT:-120}"
exec 9>"${LOCK_FILE}"
flock -w "${LOCK_TIMEOUT}" 9 || fail "could not acquire write lock within ${LOCK_TIMEOUT}s"

# --- Pull ---------------------------------------------------------------------
# --ff-only is deliberate. This sync is the PULL half of a bidirectional model:
# Pi-Alfred also WRITES to the vault (card T). The design contract is that Alfred
# commits AND pushes immediately on every write, so the Pi never diverges from
# origin and a fast-forward always suffices. Fast-forward-only keeps the Pi a
# clean mirror and fails loudly instead of auto-merging into a broken tree.
# Do not switch to a merge/rebase pull without revisiting that contract.
if output="$(git -C "${VAULT_PATH}" pull --ff-only 2>&1)"; then
    log "OK: ${output//$'\n'/ | }"
    exit 0
fi

# Pull failed — classify the reason so the log says something useful.
# Common cases: network down, auth failure (deploy key), or local divergence.
case "${output}" in
    *"Could not resolve host"*|*"Network is unreachable"*|*"Connection timed out"*|*"Temporary failure in name resolution"*)
        fail "network unavailable — could not reach origin: ${output//$'\n'/ | }"
        ;;
    *"Permission denied"*|*"Authentication failed"*|*"Host key verification failed"*)
        fail "authentication failed — check the SSH deploy key: ${output//$'\n'/ | }"
        ;;
    *"Not possible to fast-forward"*|*"non-fast-forward"*|*"divergent branches"*|*"have diverged"*)
        fail "local vault has diverged from origin — resolve by hand, not merging: ${output//$'\n'/ | }"
        ;;
    *)
        fail "pull failed: ${output//$'\n'/ | }"
        ;;
esac
