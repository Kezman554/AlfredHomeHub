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
# reach the Pi. This is a trusted home LAN.
HOST = os.getenv("VAULT_API_HOST", "0.0.0.0")  # noqa: S104

# Identity on vault commits made by the write endpoints, so `git log` in the
# vault shows at a glance which changes Alfred made.
GIT_USER_NAME = os.getenv("VAULT_GIT_NAME", "Alfred")
GIT_USER_EMAIL = os.getenv("VAULT_GIT_EMAIL", "alfred@alfred.local")

# How long a write waits for the lock shared with the vault-sync cron before
# giving up with a 503. The sync's pull takes seconds, so 30 is generous.
WRITE_LOCK_TIMEOUT = float(os.getenv("VAULT_WRITE_LOCK_TIMEOUT", "30"))

# Per-git-command timeout (pull/push go to GitHub over the network).
GIT_TIMEOUT = float(os.getenv("VAULT_GIT_TIMEOUT", "60"))
