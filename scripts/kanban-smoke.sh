#!/usr/bin/env bash
# Three-line smoke check for the Kanban API on the Pi: container running, API
# healthy, and the board's card count agrees between the API and the DB copy.
# Run on the Pi. Exits non-zero on the first failure.
set -euo pipefail
API_BASE="${API_BASE:-http://localhost:8300}"
DB_PATH="${DB_PATH:-/home/kezman554/kanban-data/kanban.db}"

docker ps --filter name=kanban-api --filter status=running --format '{{.Names}}' | grep -qx kanban-api || { echo "FAIL: kanban-api container not running"; exit 1; }
curl -sf -m 10 "${API_BASE}/health" >/dev/null || { echo "FAIL: ${API_BASE}/health unreachable"; exit 1; }
api=$(curl -sf -m 10 "${API_BASE}/stats" | python3 -c 'import json,sys; print(json.load(sys.stdin)["cards"])'); dbc=$(docker exec kanban-api node -e 'console.log(require("better-sqlite3")(process.env.KANBAN_DB_PATH).prepare("SELECT COUNT(*) c FROM cards").get().c)'); [ "$api" = "$dbc" ] && echo "OK: kanban-api up, ${api} cards (API == DB)" || { echo "FAIL: API ${api} != DB ${dbc}"; exit 1; }
