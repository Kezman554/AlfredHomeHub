"""Service configuration, all overridable by environment variable."""

from __future__ import annotations

import os
from pathlib import Path

# Where the vault is mounted inside the container. On the Pi the host path
# /home/kezman554/alfred-vault (kept current by the card-L sync cron) is
# bind-mounted here read-only; see docker/docker-compose.yml.
VAULT_PATH = Path(os.getenv("VAULT_PATH", "/vault"))

# Port the API listens on. NOT 8123 — Home Assistant owns that.
PORT = int(os.getenv("VAULT_API_PORT", "8200"))

# Bind on all interfaces so other LAN devices (the MorningSync alarm app) can
# reach the Pi. This is a trusted home LAN and the API is read-only.
HOST = os.getenv("VAULT_API_HOST", "0.0.0.0")  # noqa: S104
