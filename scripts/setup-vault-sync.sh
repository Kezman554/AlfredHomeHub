#!/usr/bin/env bash
# One-time setup for vault sync on the Pi:
#   1. Clone the Obsidian vault (if not already present)
#   2. Install vault-sync.sh as a cron job (every 10 minutes)
#   3. Run one sync to confirm it works
#
# Idempotent: safe to re-run. Requires the SSH deploy key for the vault repo to
# already be in place — this script does not create or modify any credentials.
set -euo pipefail

# --- Config -------------------------------------------------------------------
VAULT_REPO="git@github.com:Kezman554/ObsidianVault.git"
VAULT_PATH="${VAULT_PATH:-/home/kezman554/alfred-vault}"
LOG_FILE="${LOG_FILE:-/home/kezman554/logs/vault-sync.log}"
CRON_SCHEDULE="*/10 * * * *"

SYNC_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vault-sync.sh"

# --- 1. Clone -----------------------------------------------------------------
if [ -d "${VAULT_PATH}/.git" ]; then
    echo "Vault already cloned at ${VAULT_PATH} — skipping clone."
elif [ -e "${VAULT_PATH}" ]; then
    echo "ERROR: ${VAULT_PATH} exists but is not a git repo. Move it aside and re-run." >&2
    exit 1
else
    echo "Cloning ${VAULT_REPO} into ${VAULT_PATH}..."
    # Fails fast and non-interactively if the deploy key is missing or wrong.
    git clone "${VAULT_REPO}" "${VAULT_PATH}"
fi

# --- 2. Install cron job ------------------------------------------------------
chmod +x "${SYNC_SCRIPT}"

CRON_ENTRY="${CRON_SCHEDULE} VAULT_PATH=${VAULT_PATH} LOG_FILE=${LOG_FILE} ${SYNC_SCRIPT}"

# Rebuild the crontab with any existing vault-sync line stripped out, then append
# ours. Re-running therefore replaces the entry rather than duplicating it.
{
    crontab -l 2>/dev/null | grep -Fv "${SYNC_SCRIPT}" || true
    printf '%s\n' "${CRON_ENTRY}"
} | crontab -

# --- 3. Initial sync ----------------------------------------------------------
echo "Running an initial sync..."
VAULT_PATH="${VAULT_PATH}" LOG_FILE="${LOG_FILE}" "${SYNC_SCRIPT}"

# --- Confirmation -------------------------------------------------------------
echo
echo "Vault sync installed."
echo "  Vault:  ${VAULT_PATH}"
echo "  Log:    ${LOG_FILE}"
echo "  Cron:   $(crontab -l | grep -F "${SYNC_SCRIPT}")"
