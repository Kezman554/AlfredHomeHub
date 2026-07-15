"""Tests for the /daily-schedule parser in vault_api.vault.

Pure-stdlib parser tests plus a couple against Vault reading fixture files on
disk — no Pi and no running server required.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from vault_api.vault import Vault, parse_day_schedule, week_plan_relpath

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
