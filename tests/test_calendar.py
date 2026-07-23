"""Tests for the family-calendar endpoint and its Home Assistant normalisation.

The normalisation is pure and stdlib-only, so most of this runs with no network,
no HA and no FastAPI. The HTTP tests at the bottom import TestClient lazily, the
same way test_inbox and test_shopping do, so the pure tests still run without
fastapi installed.
"""

from __future__ import annotations

from datetime import date

import pytest

from vault_api.homeassistant import (
    HomeAssistantConfigError,
    HomeAssistantError,
    fetch_events,
    normalise_events,
)

# Home Assistant's own shape, as returned by
# GET /api/calendars/calendar.young_family. The two kinds are distinguished
# structurally — "date" vs "dateTime" — which is the thing worth pinning down.
ALL_DAY_EVENT = {
    "summary": "France",
    "start": {"date": "2026-07-24"},
    "end": {"date": "2026-07-25"},
}

TIMED_EVENT = {
    "summary": "Dentist",
    "start": {"dateTime": "2026-07-24T15:00:00+01:00"},
    "end": {"dateTime": "2026-07-24T15:45:00+01:00"},
    "location": "High Street Dental",
    "description": "Oliver's check-up",
}


# --- Normalisation ------------------------------------------------------------


def test_all_day_event_is_flagged_and_keeps_plain_dates():
    result = normalise_events([ALL_DAY_EVENT], "calendar.young_family")

    assert result["calendar"] == "young_family"
    assert result["events"] == [
        {
            "summary": "France",
            "start": "2026-07-24",
            "end": "2026-07-25",
            "all_day": True,
            "location": None,
            "description": None,
        }
    ]


def test_timed_event_is_not_all_day_and_keeps_datetimes():
    result = normalise_events([TIMED_EVENT], "calendar.young_family")

    assert result["events"] == [
        {
            "summary": "Dentist",
            "start": "2026-07-24T15:00:00+01:00",
            "end": "2026-07-24T15:45:00+01:00",
            "all_day": False,
            "location": "High Street Dental",
            "description": "Oliver's check-up",
        }
    ]


def test_mixed_response_normalises_both_kinds_together():
    """The realistic case: one payload carrying both kinds."""
    result = normalise_events([TIMED_EVENT, ALL_DAY_EVENT], "calendar.young_family")

    assert [(e["summary"], e["all_day"]) for e in result["events"]] == [
        ("France", True),  # all-day sorts ahead of that day's timed events
        ("Dentist", False),
    ]


def test_events_are_sorted_by_start():
    later = {"summary": "Later", "start": {"date": "2026-07-30"}, "end": {"date": "2026-07-31"}}
    result = normalise_events([later, ALL_DAY_EVENT], "calendar.young_family")

    assert [e["summary"] for e in result["events"]] == ["France", "Later"]


def test_all_day_end_stays_exclusive():
    """Google/HA give an exclusive end for all-day events; we pass it through.

    Adjusting it here would make single-day and multi-day events disagree about
    what `end` means.
    """
    result = normalise_events([ALL_DAY_EVENT], "calendar.young_family")
    assert result["events"][0]["end"] == "2026-07-25"


def test_entity_id_becomes_bare_calendar_name():
    assert normalise_events([], "calendar.young_family")["calendar"] == "young_family"
    assert normalise_events([], "young_family")["calendar"] == "young_family"


def test_no_events_is_an_empty_list_not_an_error():
    assert normalise_events([], "calendar.young_family") == {
        "calendar": "young_family",
        "events": [],
    }


def test_untitled_event_gets_a_placeholder_summary():
    """HA omits summary for untitled entries; clients get a string, not a null."""
    result = normalise_events(
        [{"start": {"date": "2026-07-24"}, "end": {"date": "2026-07-25"}}],
        "calendar.young_family",
    )
    assert result["events"][0]["summary"] == "(no title)"


def test_blank_location_and_description_normalise_to_none():
    """HA sends "" rather than omitting the key; clients should see one falsy form."""
    result = normalise_events(
        [{**ALL_DAY_EVENT, "location": "", "description": ""}], "calendar.young_family"
    )
    assert result["events"][0]["location"] is None
    assert result["events"][0]["description"] is None


def test_flattened_string_edges_are_tolerated():
    """Some HA versions flatten start/end to bare strings rather than objects."""
    result = normalise_events(
        [
            {"summary": "Flat all-day", "start": "2026-07-24", "end": "2026-07-25"},
            {
                "summary": "Flat timed",
                "start": "2026-07-24T09:00:00+01:00",
                "end": "2026-07-24T10:00:00+01:00",
            },
        ],
        "calendar.young_family",
    )
    assert [(e["summary"], e["all_day"]) for e in result["events"]] == [
        ("Flat all-day", True),
        ("Flat timed", False),
    ]


def test_unusable_entries_are_skipped_not_fatal():
    """One malformed event from a shared calendar must not blank the panel."""
    result = normalise_events(
        [ALL_DAY_EVENT, {"summary": "No start at all"}, "not an object"],
        "calendar.young_family",
    )
    assert [e["summary"] for e in result["events"]] == ["France"]


def test_non_list_payload_is_an_error():
    with pytest.raises(HomeAssistantError):
        normalise_events({"message": "Entity not found"}, "calendar.young_family")


# --- fetch_events: config and upstream failures --------------------------------


def test_missing_token_raises_config_error(monkeypatch):
    monkeypatch.setattr("vault_api.config.HA_TOKEN", None)

    with pytest.raises(HomeAssistantConfigError) as excinfo:
        fetch_events(date(2026, 7, 24), date(2026, 7, 31))

    assert "HA_TOKEN" in str(excinfo.value)


def test_fetch_sends_bearer_token_and_iso_range(monkeypatch):
    """The request HA actually receives: auth header, entity, and both bounds."""
    captured = {}

    class FakeResponse:
        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    fetch_events(
        date(2026, 7, 24),
        date(2026, 7, 31),
        "calendar.young_family",
        base_url="http://192.168.1.100:8123",
        token="secret-token",
    )

    assert captured["url"].startswith(
        "http://192.168.1.100:8123/api/calendars/calendar.young_family?"
    )
    assert "2026-07-24T00%3A00%3A00" in captured["url"]
    assert "2026-07-31T00%3A00%3A00" in captured["url"]
    # urllib title-cases header names.
    assert captured["headers"]["Authorization"] == "Bearer secret-token"


def test_upstream_http_error_carries_status_but_not_the_token(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", hdrs=None, fp=None
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(HomeAssistantError) as excinfo:
        fetch_events(
            date(2026, 7, 24), date(2026, 7, 31), token="secret-token"
        )

    assert excinfo.value.status == 401
    assert "secret-token" not in str(excinfo.value)


def test_unreachable_home_assistant_raises_without_status(monkeypatch):
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(HomeAssistantError) as excinfo:
        fetch_events(date(2026, 7, 24), date(2026, 7, 31), token="tok")

    assert excinfo.value.status is None
    assert "could not reach Home Assistant" in str(excinfo.value)


def test_invalid_json_from_home_assistant_is_an_error(monkeypatch):
    class FakeResponse:
        def read(self):
            return b"<html>not json</html>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: FakeResponse())

    with pytest.raises(HomeAssistantError) as excinfo:
        fetch_events(date(2026, 7, 24), date(2026, 7, 31), token="tok")

    assert "invalid JSON" in str(excinfo.value)


# --- Over HTTP ----------------------------------------------------------------
#
# Proving the wiring: query defaults, the normalised body, and that upstream
# failures surface as 502/500 rather than a stack trace.


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from vault_api.app import app

    return TestClient(app)


def test_http_returns_normalised_events(client, monkeypatch):
    monkeypatch.setattr(
        "vault_api.routers.calendar.fetch_events",
        lambda start, end: normalise_events([ALL_DAY_EVENT], "calendar.young_family"),
    )

    response = client.get("/calendar/events")

    assert response.status_code == 200
    assert response.json() == {
        "calendar": "young_family",
        "events": [
            {
                "summary": "France",
                "start": "2026-07-24",
                "end": "2026-07-25",
                "all_day": True,
                "location": None,
                "description": None,
            }
        ],
    }


def test_http_defaults_to_today_through_a_week_ahead(client, monkeypatch):
    seen = {}

    def fake_fetch(start, end):
        seen["start"], seen["end"] = start, end
        return normalise_events([], "calendar.young_family")

    monkeypatch.setattr("vault_api.routers.calendar.fetch_events", fake_fetch)

    client.get("/calendar/events")

    assert seen["start"] == date.today()
    assert (seen["end"] - seen["start"]).days == 7


def test_http_passes_explicit_dates_through(client, monkeypatch):
    seen = {}

    def fake_fetch(start, end):
        seen["start"], seen["end"] = start, end
        return normalise_events([], "calendar.young_family")

    monkeypatch.setattr("vault_api.routers.calendar.fetch_events", fake_fetch)

    client.get("/calendar/events?start=2026-07-24&end=2026-07-26")

    assert seen["start"] == date(2026, 7, 24)
    assert seen["end"] == date(2026, 7, 26)


def test_http_missing_token_is_500(client, monkeypatch):
    def fake_fetch(start, end):
        raise HomeAssistantConfigError("HA_TOKEN is not set")

    monkeypatch.setattr("vault_api.routers.calendar.fetch_events", fake_fetch)

    response = client.get("/calendar/events")

    assert response.status_code == 500
    assert "HA_TOKEN" in response.json()["detail"]


def test_http_upstream_failure_is_502_with_status_and_no_token(client, monkeypatch):
    def fake_fetch(start, end):
        raise HomeAssistantError("Home Assistant returned 401", status=401)

    monkeypatch.setattr("vault_api.routers.calendar.fetch_events", fake_fetch)

    response = client.get("/calendar/events")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "401" in detail
    assert "Bearer" not in detail


def test_http_inverted_range_is_422(client, monkeypatch):
    """HA answers [] for an inverted range, which reads as "nothing on"."""
    monkeypatch.setattr(
        "vault_api.routers.calendar.fetch_events",
        lambda start, end: pytest.fail("should not reach Home Assistant"),
    )

    response = client.get("/calendar/events?start=2026-07-30&end=2026-07-24")

    assert response.status_code == 422


def test_http_bad_date_is_422(client):
    assert client.get("/calendar/events?start=not-a-date").status_code == 422
