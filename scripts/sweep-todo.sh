#!/usr/bin/env bash
# Sweep ticked items off the rolling to-do, via the vault API's sweep endpoint.
# Intended to run unattended from cron overnight (see setup-todo-sweep.sh), and
# equally runnable by hand.
#
# Deliberately a thin trigger: the sweep itself (lock, pull, edit, commit,
# push) lives in the vault API, so this and the on-demand "clear completed"
# button share one code path. If the API is down, this logs the failure and
# exits non-zero — it never touches the vault directly.
set -euo pipefail

# --- Config -------------------------------------------------------------------
SWEEP_URL="${SWEEP_URL:-http://localhost:8200/chalkboard/sweep}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/todo-sweep.log}"
CURL_TIMEOUT="${CURL_TIMEOUT:-120}"

# --- Logging ------------------------------------------------------------------
mkdir -p "$(dirname "${LOG_FILE}")"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"
}

fail() {
    log "ERROR: $*"
    exit 1
}

# --- Sweep --------------------------------------------------------------------
if ! response="$(curl -sS --fail-with-body -X POST -m "${CURL_TIMEOUT}" "${SWEEP_URL}" 2>&1)"; then
    fail "sweep request failed: ${response//$'\n'/ | }"
fi

# Summarise the JSON response for the log. The combined sweep clears the
# rolling to-do AND every active shopping list, so report both counts — a
# shopping-only sweep must not log as a no-op.
summary="$(python3 -c '
import json, sys
data = json.loads(sys.argv[1])
todo = data.get("count", 0)
shop = data.get("shopping_count", 0)
if not todo and not shop:
    print("no-op: nothing ticked")
else:
    bits = []
    if todo:
        bits.append(str(todo) + " to-do: " + "; ".join(data["swept"]))
    if shop:
        items = [s["item"] + " (from " + s["list"] + ")" for s in data["shopping_swept"]]
        bits.append(str(shop) + " shopping: " + "; ".join(items))
    print("swept " + "; ".join(bits))
' "${response}" 2>&1)" || fail "unexpected response from sweep endpoint: ${response//$'\n'/ | }"

log "OK: ${summary}"
