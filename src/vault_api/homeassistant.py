"""Read the family calendar through Home Assistant's REST API.

The calendar is a shared Google Calendar ("Young Family") linked into HA. We go
through HA rather than Google directly because HA already holds the Google OAuth
credentials and refreshes them — this service needs no Google auth of its own.

Split deliberately in two:

  normalise_events()  pure, stdlib-only, no network. HA's shape in, our shape
                      out. This is where the all-day/timed distinction is
                      decided, and it is what the tests exercise directly.
  fetch_events()      the HTTP call, which does nothing but hand HA's JSON to
                      normalise_events().

Read-only. Creating and updating events is a later card; nothing here writes.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

from . import config

log = logging.getLogger(__name__)


class HomeAssistantConfigError(RuntimeError):
    """HA is not configured — a missing token, typically. Not retryable."""


class HomeAssistantError(RuntimeError):
    """HA was unreachable or answered with a non-200.

    Carries the upstream status when there was one (None if the connection
    never got that far), so the router can report it without inventing detail.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _edge(value: object) -> tuple[str | None, bool]:
    """Normalise one end of an HA event into (iso_string, is_all_day).

    HA distinguishes the two kinds structurally, not by a flag:
        all-day  {"date": "2026-07-24"}
        timed    {"dateTime": "2026-07-24T18:00:00+01:00"}
    Presence of "date" is therefore the whole test. A bare string is tolerated
    because some HA versions and calendar integrations flatten it that way; a
    "T" in it means a datetime.
    """
    if isinstance(value, dict):
        if value.get("date") is not None:
            return str(value["date"]), True
        if value.get("dateTime") is not None:
            return str(value["dateTime"]), False
        return None, False

    if isinstance(value, str):
        return value, "T" not in value

    return None, False


def normalise_events(payload: object, entity_id: str | None = None) -> dict:
    """Turn HA's calendar response into the shape clients consume.

    Returns {"calendar": <name>, "events": [...]} with events sorted by start,
    each one:

        {"summary", "start", "end", "all_day", "location", "description"}

    `start`/`end` are plain ISO strings — a date for all-day events, a datetime
    otherwise — so a client can render them without knowing HA's schema.

    All-day `end` is passed through EXCLUSIVE, exactly as Google and HA give it:
    a one-day event on the 24th ends "2026-07-25". Quietly subtracting a day
    here would make single-day and multi-day events disagree about what `end`
    means, so the boundary keeps upstream's convention.

    Malformed entries are skipped rather than raising: one bad event from a
    shared calendar should not blank the whole dashboard panel.
    """
    # Read config at call time, not import time, so the value is whatever the
    # process is actually configured with (and so tests can monkeypatch it).
    entity_id = entity_id or config.HA_CALENDAR_ENTITY

    # The entity id is calendar.young_family; clients want "young_family".
    name = entity_id.split(".", 1)[1] if "." in entity_id else entity_id

    if not isinstance(payload, list):
        raise HomeAssistantError(
            f"expected a list of events from Home Assistant, got {type(payload).__name__}"
        )

    events: list[dict] = []
    for raw in payload:
        if not isinstance(raw, dict):
            log.warning("skipping non-object calendar entry: %r", type(raw).__name__)
            continue

        start, start_all_day = _edge(raw.get("start"))
        end, end_all_day = _edge(raw.get("end"))

        if start is None:
            # No usable start: unrenderable and unsortable, so drop it.
            log.warning("skipping calendar entry with no usable start: %r", raw.get("summary"))
            continue

        events.append(
            {
                # HA omits summary for untitled events; give clients a string
                # rather than a null they would each have to handle.
                "summary": raw.get("summary") or "(no title)",
                "start": start,
                "end": end,
                # The start decides it. An event with an all-day start and a
                # timed end is malformed; trusting the start keeps rendering
                # predictable.
                "all_day": start_all_day,
                "location": raw.get("location") or None,
                "description": raw.get("description") or None,
            }
        )
        if end is not None and end_all_day != start_all_day:
            log.warning("calendar entry mixes all-day and timed edges: %r", raw.get("summary"))

    # Sort by the ISO string: dates and datetimes both sort correctly this way
    # within a kind, and "2026-07-24" < "2026-07-24T09:00" puts an all-day event
    # ahead of that day's timed ones, which is the order a dashboard wants.
    events.sort(key=lambda event: event["start"])

    return {"calendar": name, "events": events}


def fetch_events(
    start: date,
    end: date,
    entity_id: str | None = None,
    *,
    base_url: str | None = None,
    token: str | None = None,
    timeout: float | None = None,
) -> dict:
    """Fetch and normalise events in [start, end) from Home Assistant.

    Raises HomeAssistantConfigError when no token is configured, and
    HomeAssistantError when HA is unreachable or answers non-200.

    Every setting falls back to config at CALL time rather than being bound as a
    default at import, so the process picks up its real environment and tests can
    monkeypatch without reloading the module.
    """
    entity_id = entity_id or config.HA_CALENDAR_ENTITY
    base_url = (base_url or config.HA_BASE_URL).rstrip("/")
    token = token or config.HA_TOKEN
    timeout = config.HA_TIMEOUT if timeout is None else timeout

    if not token:
        raise HomeAssistantConfigError(
            "HA_TOKEN is not set — the family calendar is read through Home "
            "Assistant, which needs a long-lived access token."
        )

    # HA wants ISO8601 instants. Local midnight on each boundary covers whole
    # days; the container shares the Pi's clock (/etc/localtime is mounted), so
    # "local" here is the household's own day, not UTC.
    start_iso = datetime.combine(start, datetime.min.time()).isoformat()
    end_iso = datetime.combine(end, datetime.min.time()).isoformat()

    url = (
        f"{base_url}/api/calendars/{entity_id}"
        f"?start={urllib.parse.quote(start_iso)}&end={urllib.parse.quote(end_iso)}"
    )

    request = urllib.request.Request(  # noqa: S310 - fixed http(s) URL from config
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        # Report the upstream status and nothing else. HA's error bodies can
        # echo the request, and the token must never reach a client or a log.
        raise HomeAssistantError(
            f"Home Assistant returned {exc.code} for {entity_id}", status=exc.code
        ) from None
    except urllib.error.URLError as exc:
        raise HomeAssistantError(
            f"could not reach Home Assistant at {base_url}: {exc.reason}"
        ) from None
    except TimeoutError:
        raise HomeAssistantError(
            f"Home Assistant did not respond within {timeout:g}s"
        ) from None

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HomeAssistantError(f"Home Assistant returned invalid JSON: {exc}") from None

    return normalise_events(payload, entity_id)
