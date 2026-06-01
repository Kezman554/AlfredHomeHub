#!/usr/bin/env bash
# Set up and start the Home Assistant container on the Pi.
# Run from anywhere; paths are resolved relative to this script.
set -euo pipefail

# --- Config (edit if the repo layout changes) ---------------------------------
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../docker" && pwd)"
HA_CONFIG_DIR="${DOCKER_DIR}/ha-config"
HA_PORT=8123

# --- Setup --------------------------------------------------------------------
# Create the config directory before first launch so the bind mount has a
# real host directory to map (otherwise Docker creates it as root).
mkdir -p "${HA_CONFIG_DIR}"

# Start Home Assistant in the background.
docker compose -f "${DOCKER_DIR}/docker-compose.yml" up -d

# --- Access URL ---------------------------------------------------------------
echo
echo "Home Assistant is starting up."
echo "Access it at: http://$(hostname):${HA_PORT}"
