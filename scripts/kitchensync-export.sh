#!/usr/bin/env bash
# Trigger the KitchenSync vault snapshot, via the API's export endpoint.
# Intended to run unattended from cron (see setup-kitchensync-export.sh), and
# equally runnable by hand.
#
# Deliberately a thin trigger: the snapshot itself (render the files, take the
# shared vault lock, pull, commit as Alfred, push) lives in KitchenSync's
# POST /api/export/run, so this cron and any "sync now" button share ONE code
# path. If the API is down, this logs the failure and exits non-zero — it never
# touches the vault directly. (Same shape as kanban-export.sh.)
set -euo pipefail

# --- Config -------------------------------------------------------------------
EXPORT_URL="${EXPORT_URL:-http://localhost:8400/api/export/run}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/kitchensync-export.log}"
CURL_TIMEOUT="${CURL_TIMEOUT:-180}"

# --- Logging ------------------------------------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"
log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"; }
fail() { log "ERROR: $*"; exit 1; }

# --- Trigger ------------------------------------------------------------------
if ! response="$(curl -sS --fail-with-body -X POST -m "${CURL_TIMEOUT}" "${EXPORT_URL}" 2>&1)"; then
    fail "snapshot request failed: ${response//$'\n'/ | }"
fi

# Summarise the JSON response for the log. "nochange" is the diff guard: the
# snapshot carries no generated-at timestamp, so an unchanged household means an
# unchanged file and no commit. Most nights SHOULD log the no-op.
summary="$(python3 -c '
import json, sys
data = json.loads(sys.argv[1])
status = data.get("status", "?")
meals = data.get("meals", "?")
pantry = data.get("pantry_items", "?")
if status == "nochange":
    print("no-op: vault already up to date")
else:
    print(f"{status}: {meals} meals, {pantry} pantry items")
' "${response}" 2>&1)" || fail "unexpected response from snapshot endpoint: ${response//$'\n'/ | }"

log "OK: ${summary}"
