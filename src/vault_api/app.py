"""Vault API — REST access to the Obsidian vault for LAN clients.

Serves vault content to the MorningSync alarm app and accepts writes to the
rolling to-do, the discovered family of shopping lists, and the inbox, each
landing as a git commit pushed to the vault repo. Runs as a container in the AlfredHomeHub
compose stack on the Pi; see docker/docker-compose.yml.

Endpoints live in routers/, one module per resource.

Most read the vault. The family calendar does not: it is a shared Google
Calendar linked into Home Assistant, and calendar.py reads it through HA's REST
API (HA holds the Google credentials, so this service holds none). It is the one
router that needs no Vault at all.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routers import calendar, chalkboard, health, inbox, schedule, shopping
from .vault import VaultBusyError, VaultSyncError, VaultWriteError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Alfred Vault API",
    description=(
        "Obsidian vault content for LAN clients: reads, plus rolling to-do, "
        "shopping-list, and inbox writes; and the family calendar, read "
        "through Home Assistant."
    ),
    version="0.6.0",
)

app.include_router(health.router)
app.include_router(chalkboard.router)
app.include_router(schedule.router)
app.include_router(shopping.router)
app.include_router(inbox.router)
app.include_router(calendar.router)


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
