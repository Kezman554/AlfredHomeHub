"""Daily schedule: today's planned items, from the current week's plan file."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..dependencies import get_vault
from ..vault import Vault

router = APIRouter(tags=["schedule"])


@router.get("/daily-schedule")
def get_daily_schedule(vault: Vault = Depends(get_vault)) -> list[dict[str, str | None]]:
    """Today's items from 1-daily/reviews/YYYY-Wnn-plan.md.

    Each item is {"task", "period"} with period "am"/"pm"/null. Returns [] with
    a 200 when this week has no plan file, today has no section, or the vault is
    unavailable — the alarm app should render an empty schedule, not an error.
    """
    return [item.to_json() for item in vault.daily_schedule_items()]
