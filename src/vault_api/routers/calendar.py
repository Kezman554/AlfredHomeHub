"""Family calendar: read-only view of the shared Google Calendar, via Home Assistant.

Unlike the other routers this one reads nothing from the vault — the calendar
lives in Google, linked into Home Assistant, which holds the OAuth credentials.
See vault_api.homeassistant for the client and the normalisation.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from .. import config
from ..homeassistant import (
    HomeAssistantConfigError,
    HomeAssistantError,
    fetch_events,
)

router = APIRouter(tags=["calendar"])


@router.get("/calendar/events")
def get_calendar_events(
    start: date | None = Query(
        None, description="ISO date, inclusive. Defaults to today (Pi local time)."
    ),
    end: date | None = Query(
        None,
        description="ISO date, exclusive. Defaults to start + 7 days.",
    ),
) -> dict:
    """Events on the family calendar between start and end.

    Both parameters are optional: with neither, this is "the week ahead" —
    today through today + 7 days, which is what the dashboard panel and the
    DailySync tile both want.

        {"calendar": "young_family",
         "events": [{"summary", "start", "end", "all_day", "location",
                     "description"}]}

    All-day events carry a plain date and `all_day: true`; timed events carry an
    ISO datetime. `end` follows Google's convention for all-day events and is
    EXCLUSIVE — a single day on the 24th ends "2026-07-25".

    Read-only. Creating and updating events is a later card.
    """
    start = start or date.today()
    end = end or start + timedelta(days=config.CALENDAR_DEFAULT_DAYS)

    # Caught here rather than left to HA: an inverted range silently returns []
    # upstream, which reads as "nothing on" instead of "you asked wrongly".
    if end < start:
        raise HTTPException(
            status_code=422,
            detail=f"end ({end.isoformat()}) is before start ({start.isoformat()})",
        )

    try:
        return fetch_events(start, end)
    except HomeAssistantConfigError as exc:
        # A deployment fault, not a client one: the service is misconfigured and
        # no retry or different request will help.
        raise HTTPException(status_code=500, detail=str(exc)) from None
    except HomeAssistantError as exc:
        # Upstream failed. 502 with its status where there was one — never the
        # token, and never HA's raw body, which can echo the request.
        detail = str(exc)
        if exc.status is not None:
            detail = f"{detail} (upstream status {exc.status})"
        raise HTTPException(status_code=502, detail=detail) from None
