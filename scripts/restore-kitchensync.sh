#!/usr/bin/env bash
# Restore a KitchenSync backup archive into a Docker volume.
#
# This is the half of the backup that people skip, and it is the half that
# decides whether the backup was ever real. Run it as a DRILL against a scratch
# volume regularly — especially before a migration over household data.
#
# Usage:
#   # DRILL — restore into a scratch volume and verify, touching nothing live:
#   restore-kitchensync.sh --drill [ARCHIVE]
#
#   # REAL — overwrite the live volume (prompts; stops the app first):
#   restore-kitchensync.sh --live ARCHIVE
#
# With no ARCHIVE the newest archive in BACKUP_DIR is used.
#
# Verification performed either way:
#   - the archive extracts
#   - kitchensync.db opens and passes PRAGMA integrity_check
#   - its Alembic version matches the app image's head (a DB restored at an
#     older revision needs a migration before it will serve)
#   - every recipe image referenced by the DB resolves to a file in media/
set -euo pipefail

# --- Config -------------------------------------------------------------------
BACKUP_DIR="${BACKUP_DIR:-/home/kezman554/backups/kitchensync}"
LIVE_VOLUME="${LIVE_VOLUME:-kitchensync-data}"
DRILL_VOLUME="${DRILL_VOLUME:-kitchensync-restore-drill}"
CONTAINER="${CONTAINER:-kitchensync}"
COMPOSE_FILE="${COMPOSE_FILE:-/home/kezman554/projects/AlfredHomeHub/docker/docker-compose.yml}"
IMAGE="${IMAGE:-docker-kitchensync}"

MODE=""
ARCHIVE=""
for arg in "$@"; do
    case "${arg}" in
        --drill) MODE="drill" ;;
        --live)  MODE="live" ;;
        -*)      echo "unknown flag: ${arg}" >&2; exit 2 ;;
        *)       ARCHIVE="${arg}" ;;
    esac
done
[ -n "${MODE}" ] || { echo "specify --drill or --live" >&2; exit 2; }

# --- Pick the archive ---------------------------------------------------------
if [ -z "${ARCHIVE}" ]; then
    ARCHIVE="$(ls -1t "${BACKUP_DIR}"/kitchensync-*.tar.gz 2>/dev/null | head -1 || true)"
    [ -n "${ARCHIVE}" ] || { echo "no archives in ${BACKUP_DIR}" >&2; exit 1; }
fi
[ -f "${ARCHIVE}" ] || { echo "no such archive: ${ARCHIVE}" >&2; exit 1; }

ARCHIVE_DIR="$(cd "$(dirname "${ARCHIVE}")" && pwd)"
ARCHIVE_NAME="$(basename "${ARCHIVE}")"

echo "Archive: ${ARCHIVE}"
echo "Mode:    ${MODE}"

# --- Choose the target volume -------------------------------------------------
if [ "${MODE}" = "drill" ]; then
    TARGET="${DRILL_VOLUME}"
    # Scratch: destroy any previous drill so the test starts from nothing and
    # cannot pass on last time's data.
    docker volume rm "${TARGET}" >/dev/null 2>&1 || true
    docker volume create "${TARGET}" >/dev/null
    echo "Target:  ${TARGET} (scratch — the live volume is untouched)"
else
    TARGET="${LIVE_VOLUME}"
    echo "Target:  ${TARGET}  ** LIVE — this REPLACES current household data **"
    printf 'Type RESTORE to continue: '
    read -r confirm
    [ "${confirm}" = "RESTORE" ] || { echo "aborted"; exit 1; }
    docker compose -f "${COMPOSE_FILE}" stop "${CONTAINER}" >/dev/null 2>&1 || true
fi

# --- Extract ------------------------------------------------------------------
# Clear the target first: an overlay of new files onto old ones is not a
# restore, it is a merge, and it can leave rows pointing at images from a
# different era.
docker run --rm -v "${TARGET}":/data busybox \
    sh -c 'rm -rf /data/* /data/..?* 2>/dev/null || true'

docker run --rm \
    -v "${TARGET}":/data \
    -v "${ARCHIVE_DIR}":/backup:ro \
    busybox tar xzf "/backup/${ARCHIVE_NAME}" -C /data

echo "Extracted."

# --- Verify -------------------------------------------------------------------
# Run inside the app image: same Python, same SQLite, same Alembic as production
# — a restore that only verifies with host tools proves less.
echo
echo "--- Verification ---"
# set +e so a FAILED verification still reaches the restart below: in --live mode
# leaving the household's app stopped because the check failed is the worst
# possible outcome. rc is reported at the end.
set +e
# -i is essential: without it the container gets no stdin, `python -` reads EOF,
# runs an empty program and exits 0 — a verification that always "passes".
docker run --rm -i \
    -v "${TARGET}":/data \
    -e DATABASE_URL=sqlite:////data/kitchensync.db \
    -e MEDIA_DIR=/data/media \
    -e AUTO_MIGRATE=false \
    "${IMAGE}" python - <<'PY'
import os, sqlite3, sys
from pathlib import Path

db_path = Path("/data/kitchensync.db")
media = Path("/data/media")
problems = []

if not db_path.exists():
    print("FAIL: no kitchensync.db in the archive")
    sys.exit(1)
print(f"ok   database present ({db_path.stat().st_size} bytes)")

con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

integrity = con.execute("PRAGMA integrity_check;").fetchone()[0]
if integrity != "ok":
    problems.append(f"integrity_check: {integrity}")
else:
    print("ok   integrity_check passed")

# Alembic revision in the restored DB vs the head this image ships. A restore
# sitting at an older revision still needs its migration before it will serve.
try:
    restored = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
except Exception as exc:
    problems.append(f"no alembic_version table ({exc}) — schema was not migration-managed")
    restored = None

if restored:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    os.chdir("/app")
    script = ScriptDirectory.from_config(Config("/app/alembic.ini"))
    head = script.get_current_head()
    if restored == head:
        print(f"ok   schema at head ({restored})")
    else:
        problems.append(f"schema at {restored}, image head is {head} — needs migrating")

# Row counts, so an empty-but-valid database cannot pass as a good restore.
counts = {}
for table in ("recipes", "meal_plans", "pantry_items", "shopping_list_items",
              "ingredients", "activity_log"):
    try:
        counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error as exc:
        # Every one of these is in the baseline schema, so a missing table means
        # a bad restore — not something to paper over with "n/a".
        counts[table] = "MISSING"
        problems.append(f"table {table} missing from restored DB ({exc})")
print("ok   rows: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

# Every image a LIVE recipe references must exist on disk, or the restore
# silently yields recipes with broken photos.
#
# Scoped to is_deleted = 0 on purpose. Recipe deletion is a SOFT delete: the row
# survives with its image path intact. A soft-deleted recipe whose file has since
# been cleaned up is not a broken restore, and failing on it would make the drill
# cry wolf forever over data the household already threw away. Those are counted
# and reported, just not treated as failures.
recipe_cols = [c[1] for c in con.execute("PRAGMA table_info(recipes)")]
live_filter = " AND is_deleted = 0" if "is_deleted" in recipe_cols else ""

try:
    refs = [r[0] for r in con.execute(
        "SELECT image FROM recipes WHERE image IS NOT NULL AND image != ''"
        + live_filter)]
except sqlite3.Error:
    refs = []

missing = []
for ref in refs:
    name = ref.rsplit("/", 1)[-1]
    if not (media / name).is_file():
        missing.append(ref)

if refs:
    if missing:
        problems.append(f"{len(missing)}/{len(refs)} referenced images missing: {missing[:5]}")
    else:
        print(f"ok   all {len(refs)} live referenced images resolve in media/")
else:
    print("ok   no live image references to check")

# Soft-deleted recipes with vanished images: reported, never fatal.
if live_filter:
    dangling = 0
    for (ref,) in con.execute(
        "SELECT image FROM recipes WHERE image IS NOT NULL AND image != ''"
        " AND is_deleted = 1"
    ):
        if not (media / ref.rsplit("/", 1)[-1]).is_file():
            dangling += 1
    if dangling:
        print(f"note {dangling} deleted recipe(s) reference images no longer on"
              " disk — expected after cleanup, not a restore fault")

n_media = len(list(media.glob("*"))) if media.is_dir() else 0
print(f"ok   media/ holds {n_media} file(s)")

if problems:
    print()
    for p in problems:
        print(f"FAIL: {p}")
    sys.exit(1)

print()
print("RESTORE VERIFIED")
PY
rc=$?
set -e

echo
if [ "${MODE}" = "live" ]; then
    docker compose -f "${COMPOSE_FILE}" start "${CONTAINER}" >/dev/null 2>&1 || true
    echo "App restarted."
else
    echo "Drill volume ${TARGET} left in place for inspection."
    echo "Remove it with: docker volume rm ${TARGET}"
fi

exit "${rc}"
