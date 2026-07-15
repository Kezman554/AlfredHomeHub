"""Vault access layer.

All filesystem access to the Obsidian vault goes through here, so routers never
touch paths directly. The vault is currently mounted read-only; write-back
(appending a to-do, ticking one off) belongs on this class as write_* methods
and does not require the read paths to change.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

# Relative to the vault root. Kept here rather than inline in a router so other
# note types (daily schedule, family calendar) can be added alongside it.
ROLLING_TODO = "6-life/rolling-todo.md"

# Weekly plan files, one per ISO week: 1-daily/reviews/YYYY-Wnn-plan.md.
PLAN_DIR = "1-daily/reviews"

# Three-letter month abbreviations, index 1..12. Fixed rather than derived from
# calendar.month_abbr so parsing does not depend on the container's locale.
_MONTH_ABBR = (
    "",
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
)

# An unchecked markdown task: "- [ ] description (2026-07-14)".
# Leading whitespace allows for nesting; "*" is accepted as a bullet too.
# Checked items ("- [x]") deliberately do not match.
_UNCHECKED_ITEM = re.compile(r"^\s*[-*]\s+\[ \]\s+(?P<body>.+?)\s*$")

# A trailing capture date in parentheses. Only stripped when it ends the line.
_TRAILING_DATE = re.compile(r"^(?P<task>.*?)\s*\((?P<date>\d{4}-\d{2}-\d{2})\)$")


@dataclass(frozen=True)
class TodoItem:
    """One unchecked item from the rolling to-do."""

    task: str
    date: str | None = None

    def to_json(self) -> dict[str, str]:
        # `date` is omitted entirely when the line carried no capture date,
        # rather than serialised as null.
        return {k: v for k, v in asdict(self).items() if v is not None}


def parse_unchecked_items(text: str) -> list[TodoItem]:
    """Extract unchecked to-do items from markdown, in document order."""
    items: list[TodoItem] = []
    for line in text.splitlines():
        match = _UNCHECKED_ITEM.match(line)
        if not match:
            continue

        body = match.group("body")
        dated = _TRAILING_DATE.match(body)
        if dated and dated.group("task"):
            items.append(TodoItem(task=dated.group("task"), date=dated.group("date")))
        else:
            # No trailing date, or the line is nothing but a date.
            items.append(TodoItem(task=body))
    return items


@dataclass(frozen=True)
class ScheduleItem:
    """One planned item from today's section of the weekly plan."""

    task: str
    period: str | None = None  # "am", "pm", or None

    def to_json(self) -> dict[str, str | None]:
        # Unlike TodoItem, `period` is always present (possibly null): the alarm
        # app keys off it and null is a meaningful "no time-of-day" answer.
        return asdict(self)


# Bullet item within a day section: "- text" or "* text", allowing nesting.
_BULLET = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$")

# A markdown heading: "### Tue 14 Jul", "#### AM". Level = number of hashes.
_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")

# A day-of-month + month inside a heading: "14 Jul", "4 July". Weekday, if
# present, sits before it and is ignored.
_HEADING_DATE = re.compile(r"\b(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,})")

# Time-of-day signals, mapped to the two buckets the alarm app cares about. Used
# both for AM/PM subheadings grouping items and for inline "AM:"-style prefixes.
# Morning-ish -> "am"; afternoon/evening/night -> "pm". Extend as plans grow.
_PERIODS = {
    "am": "am",
    "morning": "am",
    "pre-breakfast": "am",
    "pre breakfast": "am",
    "pm": "pm",
    "afternoon": "pm",
    "evening": "pm",
    "night": "pm",
}

# An inline prefix on a bullet: "AM: walk the dog" -> ("am", "walk the dog").
_INLINE_PREFIX = re.compile(r"^(?P<label>[A-Za-z][A-Za-z -]*?):\s+(?P<rest>.+)$")


def _heading_date(text: str) -> tuple[int, int] | None:
    """(day, month) parsed from a heading's text, or None if it has no date."""
    match = _HEADING_DATE.search(text)
    if not match:
        return None
    month_key = match.group("month")[:3].lower()
    if month_key not in _MONTH_ABBR:
        return None
    return int(match.group("day")), _MONTH_ABBR.index(month_key)


def _period_for(label: str) -> str | None:
    """Map a time-of-day label ("AM", "Evening", ...) to "am"/"pm", or None."""
    return _PERIODS.get(label.strip().lower())


def parse_day_schedule(text: str, today: date) -> list[ScheduleItem]:
    """Extract today's planned items from a weekly plan file.

    Finds the day heading matching `today`, then returns its bullet items in
    document order. Each item's period comes from an inline "AM:"-style prefix
    if present, else from an AM/PM subheading it falls under, else None. Returns
    [] when today has no section in the file.
    """
    lines = text.splitlines()

    # Locate today's day heading and the level it sits at.
    start = None
    day_level = 0
    for i, line in enumerate(lines):
        heading = _HEADING.match(line)
        if not heading:
            continue
        parsed = _heading_date(heading.group("text"))
        if parsed == (today.day, today.month):
            start = i + 1
            day_level = len(heading.group("hashes"))
            break
    if start is None:
        return []

    items: list[ScheduleItem] = []
    subheading_period: str | None = None
    for line in lines[start:]:
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group("hashes"))
            text_ = heading.group("text")
            # A sibling/ancestor heading, or any dated heading, ends the section.
            if level <= day_level or _heading_date(text_) is not None:
                break
            # A deeper heading may be an AM/PM grouping.
            subheading_period = _period_for(text_)
            continue

        bullet = _BULLET.match(line)
        if not bullet:
            continue

        body = bullet.group("body")
        prefix = _INLINE_PREFIX.match(body)
        if prefix and _period_for(prefix.group("label")) is not None:
            items.append(
                ScheduleItem(task=prefix.group("rest"), period=_period_for(prefix.group("label")))
            )
        else:
            items.append(ScheduleItem(task=body, period=subheading_period))
    return items


def week_plan_relpath(today: date) -> str:
    """Relative vault path of the plan file for the ISO week containing `today`.

    Uses the ISO calendar so the week number (and its year, near a year
    boundary) matches the vault's YYYY-Wnn naming, e.g. 1-daily/reviews/
    2026-W29-plan.md.
    """
    iso_year, iso_week, _ = today.isocalendar()
    return f"{PLAN_DIR}/{iso_year:04d}-W{iso_week:02d}-plan.md"


class Vault:
    """Read access to the vault on disk.

    A missing or unreadable vault is not an error the caller must handle: reads
    degrade to empty results and log a warning, so the alarm app gets a valid
    (empty) response rather than a 500 when the Pi's sync is mid-clone.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _read_text(self, relative_path: str) -> str | None:
        path = self.root / relative_path
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.warning("vault file not found: %s", path)
        except OSError as exc:
            log.warning("vault file unreadable: %s (%s)", path, exc)
        except UnicodeDecodeError as exc:
            log.warning("vault file is not valid UTF-8: %s (%s)", path, exc)
        return None

    def rolling_todo_items(self) -> list[TodoItem]:
        """Unchecked items from the rolling to-do; empty if it can't be read."""
        text = self._read_text(ROLLING_TODO)
        if text is None:
            return []
        return parse_unchecked_items(text)

    def daily_schedule_items(self, today: date | None = None) -> list[ScheduleItem]:
        """Today's planned items from the current week's plan file.

        Empty when there is no plan file for this week, no section for today, or
        the vault is unreadable — never an error the caller must handle.
        """
        today = today or date.today()
        text = self._read_text(week_plan_relpath(today))
        if text is None:
            return []
        return parse_day_schedule(text, today)
