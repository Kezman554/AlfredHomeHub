#!/usr/bin/env bash
# Export the live Kanban board into the Obsidian vault, from the Pi.
#
# Pulls the export JSON + human summary from the Kanban API (which builds them
# from the same data layer the Electron app uses), writes both into the vault,
# and commits+pushes so the laptop's session-start pull carries them back — this
# is how Claude Code sessions on the laptop read a current board without the
# Electron app exporting anything.
#
# The git side lives here (on the Pi host), NOT in the container: the container
# has no vault access. Serialises with vault-sync and the vault API's writes via
# the shared lock. Runnable by hand or from cron (see setup-kanban-export.sh).
set -euo pipefail

# --- Config -------------------------------------------------------------------
API_BASE="${API_BASE:-http://localhost:8300}"
VAULT_PATH="${VAULT_PATH:-/home/kezman554/alfred-vault}"
EXPORT_DIR="${EXPORT_DIR:-4-dev-hub}"
JSON_REL="${EXPORT_DIR}/kanban-export.json"
SUMMARY_REL="${EXPORT_DIR}/kanban-summary.md"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kanban-export.log}"
CURL_TIMEOUT="${CURL_TIMEOUT:-60}"
LOCK_TIMEOUT="${LOCK_TIMEOUT:-120}"
GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-Alfred}"
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-alfred@alfred.local}"

# --- Logging ------------------------------------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"
log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"; }
fail() { log "ERROR: $*"; exit 1; }

# --- Preconditions ------------------------------------------------------------
[ -d "${VAULT_PATH}/.git" ] || fail "no git repo at ${VAULT_PATH}"

# --- Fetch export from the API ------------------------------------------------
json_tmp="$(mktemp)"; summary_tmp="$(mktemp)"
trap 'rm -f "${json_tmp}" "${summary_tmp}"' EXIT

if ! curl -sS --fail-with-body -m "${CURL_TIMEOUT}" "${API_BASE}/export/json" -o "${json_tmp}"; then
    fail "could not fetch ${API_BASE}/export/json (is kanban-api up?)"
fi
if ! curl -sS --fail-with-body -m "${CURL_TIMEOUT}" "${API_BASE}/export/summary" -o "${summary_tmp}"; then
    fail "could not fetch ${API_BASE}/export/summary"
fi
# Cheap sanity check that we got JSON, not an error body.
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${json_tmp}" \
    || fail "export/json was not valid JSON"

# --- Lock (shared with vault-sync + vault API writes) -------------------------
exec 9>"${VAULT_PATH}/.git/alfred-write.lock"
flock -w "${LOCK_TIMEOUT}" 9 || fail "could not acquire vault write lock within ${LOCK_TIMEOUT}s"

# --- Pull, write, commit, push ------------------------------------------------
# --ff-only for the same reason as vault-sync: the Pi is a mirror and every
# writer pushes immediately. A divergence is a real problem, so fail loudly.
git -C "${VAULT_PATH}" pull --ff-only >/dev/null 2>&1 \
    || fail "vault pull --ff-only failed — resolve by hand"

# Write via install (atomic-ish rename) so a reader never sees a half file.
install -m 644 "${json_tmp}"    "${VAULT_PATH}/${JSON_REL}"
install -m 644 "${summary_tmp}" "${VAULT_PATH}/${SUMMARY_REL}"

git -C "${VAULT_PATH}" add -- "${JSON_REL}" "${SUMMARY_REL}"
if git -C "${VAULT_PATH}" diff --cached --quiet; then
    log "OK: no change to export"
    exit 0
fi

if ! git -C "${VAULT_PATH}" \
        -c "user.name=${GIT_AUTHOR_NAME}" -c "user.email=${GIT_AUTHOR_EMAIL}" \
        commit -q -m "alfred kanban: export board to vault" >/dev/null 2>&1; then
    git -C "${VAULT_PATH}" reset -q --hard '@{upstream}' || true
    fail "commit failed — vault reset to origin"
fi

if ! git -C "${VAULT_PATH}" push -q >/dev/null 2>&1; then
    git -C "${VAULT_PATH}" reset -q --hard '@{upstream}' || true
    fail "push failed — vault reset to origin"
fi

projects="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["projects"]))' "${json_tmp}")"
log "OK: exported ${projects} projects, committed and pushed"
