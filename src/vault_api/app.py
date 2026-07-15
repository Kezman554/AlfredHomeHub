"""Vault API — read-only REST access to the Obsidian vault for LAN clients.

Serves vault content to the MorningSync alarm app. Runs as a container in the
AlfredHomeHub compose stack on the Pi; see docker/docker-compose.yml.

Endpoints live in routers/ — adding /daily-schedule or family-calendar later is
a new router plus a read method on Vault, with nothing here to unpick.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .routers import chalkboard, health, schedule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Alfred Vault API",
    description="Read-only access to Obsidian vault content for LAN clients.",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(chalkboard.router)
app.include_router(schedule.router)
