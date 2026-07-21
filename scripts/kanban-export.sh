#!/usr/bin/env bash
# Trigger the Kanban vault export, via the API's export endpoint.
# Intended to run unattended from cron (see setup-kanban-export.sh), and equally
# runnable by hand.
#
# Deliberately a thin trigger: the export itself (regenerate the files, take the
# shared vault lock, pull, commit as Alfred, push) lives in the Kanban API's
# POST /export/run, so this cron and the browser board's "Sync to Vault" button
# share ONE code path. If the API is down, this logs the failure and exits
# non-zero — it never touches the vault directly. (Same shape as sweep-todo.sh.)
set -euo pipefail

# --- Config -------------------------------------------------------------------
EXPORT_URL="${EXPORT_URL:-http://localhost:8300/export/run}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kanban-export.log}"
CURL_TIMEOUT="${CURL_TIMEOUT:-180}"

# --- Logging ------------------------------------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"
log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"; }
fail() { log "ERROR: $*"; exit 1; }

# --- Trigger ------------------------------------------------------------------
if ! response="$(curl -sS --fail-with-body -X POST -m "${CURL_TIMEOUT}" "${EXPORT_URL}" 2>&1)"; then
    fail "export request failed: ${response//$'\n'/ | }"
fi

# Summarise the JSON response for the log.
summary="$(python3 -c '
import json, sys
data = json.loads(sys.argv[1])
status = data.get("status", "?")
if status == "nochange":
    print("no-op: vault already up to date")
else:
    print(f"{status}: {data.get(\"cards\", \"?\")} cards, {data.get(\"projects\", \"?\")} projects")
' "${response}" 2>&1)" || fail "unexpected response from export endpoint: ${response//$'\n'/ | }"

log "OK: ${summary}"
