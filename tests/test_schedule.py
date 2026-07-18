"""Tests for the /daily-schedule parser in vault_api.vault.

Pure-stdlib parser tests plus a couple against Vault reading fixture files on
disk — no Pi and no running server required.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from vault_api.vault import (
    Vault,
    parse_day_schedule,
    plan_week_dates,
    plan_week_id,
    week_plan_relpath,
)

# A plan with inline AM:/PM: (and Evening:/Pre-breakfast:) prefixes on Tuesday,
# an AM/PM subheading grouping on Wednesday, and plain untagged bullets on
# Thursday. Headings carry a weekday + date the way the vault writes them.
SAMPLE_PLAN = """\
# 2026-W29 Plan

### Mon 13 Jul
- kick-off, unrelated day

### Tue 14 Jul — errands
- Pre-breakfast: stretch
- AM: walk the dog
- PM: dentist at 3
- Evening: read to the kids
- untagged loose end

### Wed 15 Jul
#### AM
- gym
- stand-up
#### PM
- code review
- Night: lock up

### Thu 16 Jul
- water the plants
- call mum

## Notes
- not a day, must never leak into Thursday
"""


def periods_of(items):
    return [i.period for i in items]


def tasks_of(items):
    return [i.task for i in items]


def test_inline_prefixes_map_to_periods():
    items = parse_day_schedule(SAMPLE_PLAN, date(2026, 7, 14))
    assert tasks_of(items) == [
        "stretch",
        "walk the dog",
        "dentist at 3",
        "read to the kids",
        "untagged loose end",
    ]
    assert periods_of(items) == ["am", "am", "pm", "pm", None]


def test_am_pm_subheadings_group_bullets():
    items = parse_day_schedule(SAMPLE_PLAN, date(2026, 7, 15))
    assert tasks_of(items) == ["gym", "stand-up", "code review", "lock up"]
    # First two under #### AM, last two under #### PM; "Night:" prefix on the
    # last bullet still resolves to pm.
    assert periods_of(items) == ["am", "am", "pm", "pm"]


def test_plain_untagged_bullets_have_null_period():
    items = parse_day_schedule(SAMPLE_PLAN, date(2026, 7, 16))
    assert tasks_of(items) == ["water the plants", "call mum"]
    assert periods_of(items) == [None, None]


def test_following_section_does_not_leak_into_last_day():
    # The "## Notes" section sits below Thursday; its bullet must be excluded.
    items = parse_day_schedule(SAMPLE_PLAN, date(2026, 7, 16))
    assert "not a day, must never leak into Thursday" not in tasks_of(items)


def test_today_absent_from_file_returns_empty():
    # 20 Jul is not in the sample plan.
    assert parse_day_schedule(SAMPLE_PLAN, date(2026, 7, 20)) == []


def test_period_is_only_am_pm_or_none():
    for day in (14, 15, 16):
        for item in parse_day_schedule(SAMPLE_PLAN, date(2026, 7, day)):
            assert item.period in ("am", "pm", None)


def test_to_json_shape_always_has_task_and_period():
    items = parse_day_schedule(SAMPLE_PLAN, date(2026, 7, 14))
    for item in items:
        blob = item.to_json()
        assert set(blob) == {"task", "period"}
        assert isinstance(blob["task"], str)
        assert blob["period"] in ("am", "pm", None)


def test_week_plan_relpath_uses_iso_week():
    # 15 Jul 2026 is in ISO week 29.
    assert week_plan_relpath(date(2026, 7, 15)) == "1-daily/reviews/2026-W29-plan.md"
    # ISO week is zero-padded to two digits.
    assert week_plan_relpath(date(2026, 1, 5)) == "1-daily/reviews/2026-W02-plan.md"


def test_sunday_belongs_to_the_next_weeks_plan():
    # Plan weeks run Sun -> Sat: Sun 12 Jul is the first possible day of the
    # W29 plan (written that evening), not part of W28, which ended Sat 11.
    assert week_plan_relpath(date(2026, 7, 12)) == "1-daily/reviews/2026-W29-plan.md"
    assert plan_week_id(date(2026, 7, 12)) == "2026-W29"
    # Saturday still belongs to its own week.
    assert plan_week_id(date(2026, 7, 18)) == "2026-W29"


def test_plan_week_dates_run_sunday_to_saturday():
    dates = plan_week_dates(date(2026, 7, 15))  # Wed of W29
    assert dates[0] == date(2026, 7, 12)  # the Sunday before ISO Monday
    assert dates[-1] == date(2026, 7, 18)  # Saturday, never the next Sunday
    assert len(dates) == 7
    # Any day of the plan week, Sunday included, yields the same seven dates.
    assert plan_week_dates(date(2026, 7, 12)) == dates
    assert plan_week_dates(date(2026, 7, 18)) == dates


# --- Week schedule ----------------------------------------------------------


def write_plan(root: Path, week: str, text: str) -> None:
    reviews = root / "1-daily" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"{week}-plan.md").write_text(text, encoding="utf-8")


def test_week_schedule_covers_first_day_through_saturday(tmp_path):
    # SAMPLE_PLAN has sections Mon 13 - Thu 16 only; Fri and Sat are inside the
    # plan's range but sectionless, so they must appear explicitly empty.
    write_plan(tmp_path, "2026-W29", SAMPLE_PLAN)
    week = Vault(root=tmp_path).week_schedule(today=date(2026, 7, 15))

    assert week.week == "2026-W29"
    assert week.start == "2026-07-13"
    assert week.end == "2026-07-18"
    assert list(week.days) == [
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
        "2026-07-18",
    ]
    assert tasks_of(week.days["2026-07-16"]) == ["water the plants", "call mum"]
    assert week.days["2026-07-17"] == []
    assert week.days["2026-07-18"] == []


def test_week_schedule_late_started_plan_omits_days_before_the_start(tmp_path):
    late_plan = "# 2026-W29 Plan\n\n### Thu 16 Jul\n- back from trip\n\n### Sat 18 Jul\n- family day\n"
    write_plan(tmp_path, "2026-W29", late_plan)
    week = Vault(root=tmp_path).week_schedule(today=date(2026, 7, 16))

    assert week.start == "2026-07-16"
    assert week.end == "2026-07-18"
    # Days before the plan started are not part of its range at all; Friday is
    # inside the range but has no section, so it is present and empty.
    assert list(week.days) == ["2026-07-16", "2026-07-17", "2026-07-18"]
    assert week.days["2026-07-17"] == []


def test_week_schedule_sunday_started_plan_includes_sunday(tmp_path):
    # W28's real plan starts "### Sun 5 Jul" — the Sunday before ISO Monday.
    sunday_plan = "# 2026-W28 Plan\n\n### Sun 5 Jul\n- weigh-in\n\n### Mon 6 Jul\n- soul files\n"
    write_plan(tmp_path, "2026-W28", sunday_plan)
    vault = Vault(root=tmp_path)

    # Queried on that Sunday itself: the plan week is W28, starting today.
    week = vault.week_schedule(today=date(2026, 7, 5))
    assert week.week == "2026-W28"
    assert week.start == "2026-07-05"
    assert week.end == "2026-07-11"
    assert tasks_of(week.days["2026-07-05"]) == ["weigh-in"]
    assert len(week.days) == 7

    # And /daily-schedule on that Sunday serves the same slice.
    items = vault.daily_schedule_items(today=date(2026, 7, 5))
    assert tasks_of(items) == ["weigh-in"]


def test_week_schedule_no_plan_file_is_well_formed(tmp_path):
    week = Vault(root=tmp_path).week_schedule(today=date(2026, 7, 15))
    assert week.week == "2026-W29"
    assert week.start is None
    assert week.end is None
    assert week.days == {}
    assert week.to_json() == {"week": "2026-W29", "start": None, "end": None, "days": {}}


def test_week_schedule_plan_with_no_day_sections_is_no_plan(tmp_path):
    write_plan(tmp_path, "2026-W29", "# 2026-W29 Plan\n\n## Focus This Week\n- big rocks\n")
    week = Vault(root=tmp_path).week_schedule(today=date(2026, 7, 15))
    assert (week.start, week.end, week.days) == (None, None, {})


def test_daily_schedule_matches_the_week_payloads_entry_for_today(tmp_path):
    write_plan(tmp_path, "2026-W29", SAMPLE_PLAN)
    vault = Vault(root=tmp_path)
    for day in (date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 17)):
        week_entry = vault.week_schedule(today=day).days.get(day.isoformat(), [])
        assert vault.daily_schedule_items(today=day) == week_entry


def test_week_schedule_to_json_items_have_task_and_period(tmp_path):
    write_plan(tmp_path, "2026-W29", SAMPLE_PLAN)
    blob = Vault(root=tmp_path).week_schedule(today=date(2026, 7, 14)).to_json()
    assert set(blob) == {"week", "start", "end", "days"}
    for items in blob["days"].values():
        for item in items:
            assert set(item) == {"task", "period"}
            assert item["period"] in ("am", "pm", None)


# --- Vault-level tests: reading real files off disk ------------------------


def test_vault_reads_todays_items(tmp_path):
    reviews = tmp_path / "1-daily" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "2026-W29-plan.md").write_text(SAMPLE_PLAN, encoding="utf-8")

    vault = Vault(root=tmp_path)
    items = vault.daily_schedule_items(today=date(2026, 7, 14))
    assert periods_of(items) == ["am", "am", "pm", "pm", None]


def test_vault_missing_plan_file_returns_empty(tmp_path):
    # No plan file written at all.
    vault = Vault(root=tmp_path)
    assert vault.daily_schedule_items(today=date(2026, 7, 14)) == []


def test_vault_unreadable_root_returns_empty():
    vault = Vault(root=Path("/no/such/vault/anywhere"))
    assert vault.daily_schedule_items(today=date(2026, 7, 14)) == []
