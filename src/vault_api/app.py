"""Vault API — REST access to the Obsidian vault for LAN clients.

Serves vault content to the MorningSync alarm app and (as of the chalkboard
write endpoints) accepts writes to the rolling to-do, each landing as a git
commit pushed to the vault repo. Runs as a container in the AlfredHomeHub
compose stack on the Pi; see docker/docker-compose.yml.

Endpoints live in routers/ — adding /daily-schedule or family-calendar later is
a new router plus a read method on Vault, with nothing here to unpick.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routers import chalkboard, health, schedule
from .vault import VaultBusyError, VaultSyncError, VaultWriteError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Alfred Vault API",
    description="Obsidian vault content for LAN clients: reads, plus rolling to-do writes.",
    version="0.2.0",
)

app.include_router(health.router)
app.include_router(chalkboard.router)
app.include_router(schedule.router)


# Write failures always leave the vault clone clean (Vault guarantees it), so
# these map straight to retryable statuses: 503 while the sync cron holds the
# lock, 502 when git couldn't reach or update origin.
@app.exception_handler(VaultBusyError)
async def vault_busy(_: Request, exc: VaultBusyError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(VaultSyncError)
async def vault_sync_failed(_: Request, exc: VaultSyncError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(VaultWriteError)
async def vault_write_failed(_: Request, exc: VaultWriteError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})
