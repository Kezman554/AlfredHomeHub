"""Chalkboard: the rolling to-do, as consumed by the MorningSync alarm app."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_vault
from ..vault import Vault

router = APIRouter(tags=["chalkboard"])


@router.get("/chalkboard")
def get_chalkboard(vault: Vault = Depends(get_vault)) -> list[dict[str, str]]:
    """Unchecked items from 6-life/rolling-todo.md.

    Returns [] with a 200 if the vault or the file is unavailable — the alarm
    app should render an empty chalkboard, not an error.
    """
    return [item.to_json() for item in vault.rolling_todo_items()]
