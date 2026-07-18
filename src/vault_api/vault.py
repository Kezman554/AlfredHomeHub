"""Vault access layer.

All filesystem access to the Obsidian vault goes through here, so routers never
touch paths directly. Reads are plain file reads; writes are full git
transactions (lock -> pull -> surgical edit -> commit -> push) so the vault on
origin is always the source of truth and the Pi's clone never diverges.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import GIT_TIMEOUT, GIT_USER_EMAIL, GIT_USER_NAME, WRITE_LOCK_TIMEOUT

log = logging.getLogger(__name__)

# Relative to the vault root. Kept here rather than inline in a router so other
# note types (daily schedule, family calendar) can be added alongside it.
ROLLING_TODO = "6-life/rolling-todo.md"

# Where dropped (and, later, swept) items are preserved. Nothing is ever
# silently destroyed: a deleted to-do line is appended here before it goes.
COMPLETED_LOG = "6-life/completed-log.md"

# Lock file shared with scripts/vault-sync.sh so a write and the 10-minute sync
# cron serialise instead of racing. Lives under .git/ so it is inside the
# bind-mounted vault but never part of the working tree.
WRITE_LOCK = ".git/alfred-write.lock"

# Default home for newly created shopping lists. Not the rule for discovery —
# any vault .md tagged life/shopping + status: active counts, wherever it
# lives — just where CREATE LIST scaffolds new ones.
SHOPPING_DIR = "6-life/shopping"

# Sweep capture files for shopping land here, so a future vault session can
# action any playbook notes named in the swept-from list (e.g. "bought items
# go to the hardware inventory") — the sweep itself never interprets them.
INBOX_DIR = "0-inbox"

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

# A checked (ticked) task: what the overnight sweep removes.
_CHECKED_ITEM = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+(?P<body>.+?)\s*$")

# A trailing capture date in parentheses. Only stripped when it ends the line.
_TRAILING_DATE = re.compile(r"^(?P<task>.*?)\s*\((?P<date>\d{4}-\d{2}-\d{2})\)$")


@dataclass(frozen=True)
class TodoItem:
    """One unchecked item from the rolling to-do.

    `line` is the raw markdown line, byte-for-byte. Dates are not unique across
    items, so it doubles as the item's key: write clients echo it back to
    target a tick or a drop.
    """

    task: str
    line: str
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
            items.append(TodoItem(task=dated.group("task"), line=line, date=dated.group("date")))
        else:
            # No trailing date, or the line is nothing but a date.
            items.append(TodoItem(task=body, line=line))
    return items


def _task_of(line: str) -> str:
    """The task text of a raw item line, for commit messages and responses."""
    match = _UNCHECKED_ITEM.match(line)
    body = match.group("body") if match else line
    dated = _TRAILING_DATE.match(body)
    if dated and dated.group("task"):
        return dated.group("task")
    return body


# --- Shopping lists -----------------------------------------------------------
#
# A discovered family of lists rather than one hardcoded file: any vault .md
# tagged life/shopping + status: active, wherever it lives. Item lines have no
# dates (unlike the rolling to-do), and ticked items are returned too — they
# stay visible (struck through by renderers) until the sweep removes them.

# A shopping-list item, checked or not: "- [ ] item — notes" / "- [x] item".
_ITEM = re.compile(r"^\s*[-*]\s+\[(?P<box>[ xX])\]\s+(?P<body>.+?)\s*$")

# An H1 heading: "# Title". Deliberately excludes "##" and deeper (the next
# char after "#" must be whitespace).
_H1 = re.compile(r"^#\s+(?P<text>.+?)\s*$")

# Vault frontmatter block: "---\n...\n---\n". Only tags/status are needed here.
_FRONTMATTER = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n?", re.DOTALL)
_TAGS_LINE = re.compile(r"^tags:\s*\[(?P<items>.*?)\]\s*$", re.MULTILINE)
_STATUS_LINE = re.compile(r"^status:\s*(?P<value>\S+)\s*$", re.MULTILINE)

# The "**Format:** ..." convention line every list carries, used to find where
# a fresh (item-less) list's checklist section starts.
_FORMAT_LINE = re.compile(r"^\*\*Format:\*\*")

# A line that starts a new, non-checklist section: a heading of any level, or
# a markdown table row. Used to avoid inserting a new item into prose/tables.
_SECTION_BOUNDARY = re.compile(r"^(#{1,6}\s|\s*\|)")

# Characters kebab-case keeps; everything else collapses to a single hyphen.
_KEBAB_INVALID = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ShoppingItem:
    """One item from a shopping list, ticked or not.

    `line` is the raw markdown line, byte-for-byte — same key convention as
    TodoItem, since shopping items carry no date to disambiguate on.
    """

    text: str
    line: str
    ticked: bool

    def to_json(self) -> dict[str, str | bool]:
        return asdict(self)


@dataclass(frozen=True)
class ShoppingListSummary:
    """One discovered shopping list: enough to render a picker and a badge."""

    id: str  # vault-relative path — the handle every other endpoint takes
    title: str
    total: int
    unticked: int

    def to_json(self) -> dict[str, str | int]:
        return asdict(self)


@dataclass(frozen=True)
class SweepResult:
    """What one combined sweep removed, split by source."""

    todo_swept: list[str]
    shopping_swept: list[dict[str, str]]  # [{"list": title, "item": text}, ...]


def parse_shopping_items(text: str) -> list[ShoppingItem]:
    """Extract every checkbox item (ticked or not) from markdown, in order."""
    items: list[ShoppingItem] = []
    for line in text.splitlines():
        match = _ITEM.match(line)
        if not match:
            continue
        items.append(
            ShoppingItem(
                text=match.group("body"), line=line, ticked=match.group("box").lower() == "x"
            )
        )
    return items


def _frontmatter(text: str) -> tuple[list[str], str | None]:
    """(tags, status) from a note's frontmatter block; ([], None) if absent."""
    match = _FRONTMATTER.match(text)
    if not match:
        return [], None
    body = match.group("body")
    tags_match = _TAGS_LINE.search(body)
    tags = [t.strip() for t in tags_match.group("items").split(",")] if tags_match else []
    status_match = _STATUS_LINE.search(body)
    status = status_match.group("value") if status_match else None
    return tags, status


def _list_title(text: str) -> str | None:
    """The note's H1, or None if it has none."""
    for line in text.splitlines():
        match = _H1.match(line)
        if match:
            return match.group("text")
    return None


def _insertion_index(lines: list[str]) -> int:
    """Where a new checkbox item line belongs, as an index into `lines`.

    Right after the last existing item if there is one — this can never land
    inside a trailing prose/table section, since it stops at whatever already
    follows the last item. For a fresh, item-less list, right after its
    "**Format:**" line and before whatever section (heading or table) follows
    it. Failing both, the true end of file, before a trailing newline's empty
    tail element so one isn't introduced.
    """
    last_item = None
    for i, line in enumerate(lines):
        if _ITEM.match(line):
            last_item = i
    if last_item is not None:
        return last_item + 1

    format_idx = None
    for i, line in enumerate(lines):
        if _FORMAT_LINE.match(line):
            format_idx = i
    if format_idx is not None:
        for i in range(format_idx + 1, len(lines)):
            if _SECTION_BOUNDARY.match(lines[i]):
                return i

    return len(lines) - 1 if lines and lines[-1] == "" else len(lines)


def _kebab_case(name: str) -> str:
    """Lowercase, hyphen-joined slug; "" if nothing kebab-able survives."""
    return _KEBAB_INVALID.sub("-", name.strip().lower()).strip("-")


@dataclass(frozen=True)
class ScheduleItem:
    """One planned item from today's section of the weekly plan."""

    task: str
    period: str | None = None  # "am", "pm", or None

    def to_json(self) -> dict[str, str | None]:
        # Unlike TodoItem, `period` is always present (possibly null): the alarm
        # app keys off it and null is a meaningful "no time-of-day" answer.
        return asdict(self)


@dataclass(frozen=True)
class WeekSchedule:
    """The whole current plan week, keyed by ISO date.

    `days` covers every date from the plan's first day through its Saturday,
    each present even when empty, so a client can tell "no plan for this day"
    from a malformed response. A missing plan file (typically Sunday before the
    weekly review) is start/end None with no days — well-formed, not an error.
    """

    week: str
    start: str | None  # ISO date of the plan's first day, or None: no plan yet
    end: str | None  # ISO date of the plan's Saturday, or None: no plan yet
    days: dict[str, list[ScheduleItem]]

    def to_json(self) -> dict:
        return {
            "week": self.week,
            "start": self.start,
            "end": self.end,
            "days": {day: [item.to_json() for item in items] for day, items in self.days.items()},
        }


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
    return _day_section_items(text.splitlines(), today) or []


def parse_week_schedule(text: str, days: list[date]) -> dict[date, list[ScheduleItem] | None]:
    """Items for each of `days`, or None for a day with no section at all.

    None vs [] is the distinction the week endpoint needs: a day the plan never
    covered (started late) versus a day whose section simply has no bullets.
    The today endpoint is `parse_day_schedule`, a single-day slice of this same
    scan, so the two can never disagree.
    """
    lines = text.splitlines()
    return {day: _day_section_items(lines, day) for day in days}


def _day_section_items(lines: list[str], day: date) -> list[ScheduleItem] | None:
    """Bullet items of `day`'s section, or None when the file has no such heading.

    Headings carry day + month only ("### Tue 14 Jul") — the year is implied by
    which week's file this is, so matching is on (day, month).
    """
    # Locate the day heading and the level it sits at.
    start = None
    day_level = 0
    for i, line in enumerate(lines):
        heading = _HEADING.match(line)
        if not heading:
            continue
        parsed = _heading_date(heading.group("text"))
        if parsed == (day.day, day.month):
            start = i + 1
            day_level = len(heading.group("hashes"))
            break
    if start is None:
        return None

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


def _plan_week_monday(today: date) -> date:
    """Monday of the plan week containing `today`.

    Plan weeks run Sunday -> Saturday but are named for the ISO week of their
    Mon-Sat core: a Sunday belongs to the *next* ISO week's plan (the one
    written at that evening's weekly review), never the week it closes — plans
    end on Saturday.
    """
    ref = today + timedelta(days=1) if today.weekday() == 6 else today
    return ref - timedelta(days=ref.weekday())


def plan_week_id(today: date) -> str:
    """The plan week's id in the vault's naming, e.g. "2026-W29"."""
    iso_year, iso_week, _ = _plan_week_monday(today).isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def plan_week_dates(today: date) -> list[date]:
    """The seven dates a plan for `today`'s week could cover, Sunday -> Saturday."""
    monday = _plan_week_monday(today)
    return [monday + timedelta(days=offset) for offset in range(-1, 6)]


def week_plan_relpath(today: date) -> str:
    """Relative vault path of the plan file for the plan week containing `today`.

    Uses the ISO calendar so the week number (and its year, near a year
    boundary) matches the vault's YYYY-Wnn naming, e.g. 1-daily/reviews/
    2026-W29-plan.md.
    """
    return f"{PLAN_DIR}/{plan_week_id(today)}-plan.md"


class VaultWriteError(Exception):
    """Base for write failures. The working tree is clean when this is raised."""


class VaultBusyError(VaultWriteError):
    """The write lock could not be acquired in time (sync cron holding it)."""


class VaultSyncError(VaultWriteError):
    """git pull or push failed; the local vault was restored to match origin."""


class ItemNotFoundError(Exception):
    """The targeted line is not (or no longer) in the rolling to-do.

    Carries the current items so the API can hand the client a fresh list to
    retarget from — the usual cause is a stale list on the client.
    """

    def __init__(self, items: list[TodoItem]) -> None:
        super().__init__("item not found in rolling to-do")
        self.items = items


class ListNotFoundError(Exception):
    """The targeted shopping list id doesn't resolve to a discoverable list.

    Covers a missing file, a file that lost its life/shopping tag or active
    status, and a malformed id (e.g. path traversal) alike — carries current
    discovery so the client can refresh and retarget, same as a stale item.
    """

    def __init__(self, lists: list[ShoppingListSummary]) -> None:
        super().__init__("shopping list not found")
        self.lists = lists


class ShoppingItemNotFoundError(Exception):
    """The targeted line is not (or no longer) in the given shopping list."""

    def __init__(self, list_id: str, items: list[ShoppingItem]) -> None:
        super().__init__("item not found in shopping list")
        self.list_id = list_id
        self.items = items


class ShoppingListExistsError(Exception):
    """CREATE LIST targeted a filename that's already taken."""

    def __init__(self, relpath: str) -> None:
        super().__init__(f"shopping list already exists: {relpath}")
        self.relpath = relpath


class _GitError(Exception):
    """Internal: a git command failed. Translated to VaultSyncError by callers."""


class Vault:
    """Read access to the vault on disk.

    A missing or unreadable vault is not an error the caller must handle: reads
    degrade to empty results and log a warning, so the alarm app gets a valid
    (empty) response rather than a 500 when the Pi's sync is mid-clone.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _read_text(self, relative_path: str) -> str | None:
        return self._read_text_at(self.root / relative_path)

    def _read_text_at(self, path: Path) -> str | None:
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

    def week_schedule(self, today: date | None = None) -> WeekSchedule:
        """The whole current plan week: every day from its first day to Saturday.

        Days the plan covers but left without a section (or without bullets)
        appear as empty lists. No plan file, or a file with no day sections at
        all, is the "no plan this week" shape: start/end None, no days.
        """
        today = today or date.today()
        week = plan_week_id(today)
        candidates = plan_week_dates(today)
        text = self._read_text(week_plan_relpath(today))
        if text is None:
            return WeekSchedule(week=week, start=None, end=None, days={})

        sections = parse_week_schedule(text, candidates)
        covered = [day for day in candidates if sections[day] is not None]
        if not covered:
            return WeekSchedule(week=week, start=None, end=None, days={})

        first, saturday = covered[0], candidates[-1]
        days = {
            day.isoformat(): sections[day] or []
            for day in candidates
            if first <= day <= saturday
        }
        return WeekSchedule(
            week=week, start=first.isoformat(), end=saturday.isoformat(), days=days
        )

    # --- Shopping lists: reads --------------------------------------------------

    def shopping_lists(self) -> list[ShoppingListSummary]:
        """Every active shopping list in the vault, wherever it lives.

        Discovery is by content, not location: any .md whose frontmatter has
        both a life/shopping tag and status: active. Unreadable files are
        skipped (logged), not fatal to the rest of the scan.
        """
        return self._scan_shopping_lists()

    def shopping_list_items(self, list_id: str) -> tuple[ShoppingListSummary, list[ShoppingItem]]:
        """A list's summary and items. Raises ListNotFoundError if `list_id`
        doesn't resolve to a currently-discoverable active shopping list."""
        path = self._resolve_shopping_list(list_id)
        text = self._read_text_at(path) or ""
        items = parse_shopping_items(text)
        return self._list_summary(path, text, items), items

    def _scan_shopping_lists(self) -> list[ShoppingListSummary]:
        summaries: list[ShoppingListSummary] = []
        for path in sorted(self.root.rglob("*.md")):
            if ".git" in path.parts:
                continue
            text = self._read_text_at(path)
            if text is None:
                continue
            tags, status = _frontmatter(text)
            if "life/shopping" not in tags or status != "active":
                continue
            summaries.append(self._list_summary(path, text, parse_shopping_items(text)))
        return summaries

    def _list_summary(
        self, path: Path, text: str, items: list[ShoppingItem]
    ) -> ShoppingListSummary:
        relpath = path.relative_to(self.root).as_posix()
        title = _list_title(text) or path.stem
        return ShoppingListSummary(
            id=relpath,
            title=title,
            total=len(items),
            unticked=sum(1 for item in items if not item.ticked),
        )

    def _resolve_shopping_list(self, list_id: str) -> Path:
        """The on-disk path for `list_id`, iff it's a live active shopping list.

        Rejects anything that resolves outside the vault root (path traversal)
        and anything that isn't currently tagged life/shopping + active — a
        list that was completed or moved is "not found" just like a deleted
        one, and callers get current discovery to retarget from either way.
        """
        try:
            root_resolved = self.root.resolve(strict=False)
            candidate = (self.root / list_id).resolve(strict=False)
            candidate.relative_to(root_resolved)
        except (ValueError, OSError):
            raise ListNotFoundError(self._scan_shopping_lists()) from None
        if not candidate.is_file():
            raise ListNotFoundError(self._scan_shopping_lists())
        text = self._read_text_at(candidate)
        if text is None:
            raise ListNotFoundError(self._scan_shopping_lists())
        tags, status = _frontmatter(text)
        if "life/shopping" not in tags or status != "active":
            raise ListNotFoundError(self._scan_shopping_lists())
        return candidate

    def _find_shopping_item(
        self, list_id: str, lines: list[str], target_line: str, *, require_unticked: bool = False
    ) -> int:
        """Index of the exact target line, which must be an item line.

        Requiring the item shape means a write can only ever touch checkbox
        lines, never frontmatter, headings, prose, or a table row.
        """
        match = _ITEM.match(target_line)
        if match and (not require_unticked or match.group("box") == " "):
            try:
                return lines.index(target_line)
            except ValueError:
                pass
        raise ShoppingItemNotFoundError(list_id, parse_shopping_items("\n".join(lines)))

    # --- Writes ---------------------------------------------------------------
    #
    # Every write is one git transaction: take the shared lock, pull --ff-only,
    # edit only the lines involved, commit, push. If commit or push fails the
    # clone is hard-reset to origin, so a failed write never leaves the tree
    # dirty — the write simply didn't happen and the client is told so.

    def add_item(self, text: str) -> TodoItem:
        """Append '- [ ] text (today)' to the rolling to-do. Returns the item."""
        task = text.strip()
        if not task or "\n" in text or "\r" in text:
            raise ValueError("task text must be a non-empty single line")
        today = date.today().isoformat()
        line = f"- [ ] {task} ({today})"

        with self._write_lock():
            self._pull()
            path = self.root / ROLLING_TODO
            original = self._read_for_write(path)
            # Appending must not disturb existing bytes; only supply the final
            # newline if the file happens to lack one.
            prefix = original if original.endswith("\n") else original + "\n"
            path.write_text(prefix + line + "\n", encoding="utf-8")
            self._commit_and_push([ROLLING_TODO], f"alfred api: add '{task}'")
        return TodoItem(task=task, line=line, date=today)

    def tick_item(self, target_line: str) -> None:
        """Flip the targeted item's '- [ ]' to '- [x]', leaving the line in place."""
        with self._write_lock():
            self._pull()
            path = self.root / ROLLING_TODO
            original = self._read_for_write(path)
            lines = original.split("\n")
            index = self._find_item(lines, target_line)
            lines[index] = lines[index].replace("[ ]", "[x]", 1)
            path.write_text("\n".join(lines), encoding="utf-8")
            self._commit_and_push([ROLLING_TODO], f"alfred api: tick '{_task_of(target_line)}'")

    def drop_item(self, target_line: str) -> None:
        """Remove the targeted line and log it in the completed log as DROPPED.

        Distinct from ticking: this is for items no longer relevant, not done.
        The line is preserved in COMPLETED_LOG — nothing is silently destroyed.
        """
        today = date.today().isoformat()
        with self._write_lock():
            self._pull()
            path = self.root / ROLLING_TODO
            original = self._read_for_write(path)
            lines = original.split("\n")
            index = self._find_item(lines, target_line)
            dropped = lines.pop(index)
            body = _UNCHECKED_ITEM.match(dropped)
            entry = f"- dropped {today}: {body.group('body') if body else dropped}"

            path.write_text("\n".join(lines), encoding="utf-8")
            self._append_to_completed_log([entry], today)
            self._commit_and_push(
                [ROLLING_TODO, COMPLETED_LOG], f"alfred api: drop '{_task_of(target_line)}'"
            )

    # --- Shopping lists: writes -------------------------------------------------

    def add_shopping_item(self, list_id: str, text: str) -> ShoppingItem:
        """Insert a new unticked item into `list_id`. See _insertion_index for
        where — never disturbs any other line, whatever else the file holds."""
        item_text = text.strip()
        if not item_text or "\n" in text or "\r" in text:
            raise ValueError("item text must be a non-empty single line")
        line = f"- [ ] {item_text}"

        with self._write_lock():
            self._pull()
            path = self._resolve_shopping_list(list_id)
            original = self._read_for_write(path)
            lines = original.split("\n")
            lines.insert(_insertion_index(lines), line)
            path.write_text("\n".join(lines), encoding="utf-8")
            relpath = path.relative_to(self.root).as_posix()
            self._commit_and_push([relpath], f"alfred api: add '{item_text}' to {relpath}")
        return ShoppingItem(text=item_text, line=line, ticked=False)

    def tick_shopping_item(self, list_id: str, target_line: str) -> None:
        """Flip the targeted item's '- [ ]' to '- [x]', leaving the line in place."""
        with self._write_lock():
            self._pull()
            path = self._resolve_shopping_list(list_id)
            original = self._read_for_write(path)
            lines = original.split("\n")
            index = self._find_shopping_item(list_id, lines, target_line, require_unticked=True)
            lines[index] = lines[index].replace("[ ]", "[x]", 1)
            path.write_text("\n".join(lines), encoding="utf-8")
            relpath = path.relative_to(self.root).as_posix()
            self._commit_and_push([relpath], f"alfred api: tick item in {relpath}")

    def drop_shopping_item(self, list_id: str, target_line: str) -> None:
        """Remove the targeted item (ticked or not) and log it as DROPPED,
        naming the source list — nothing is ever silently destroyed."""
        today = date.today().isoformat()
        with self._write_lock():
            self._pull()
            path = self._resolve_shopping_list(list_id)
            original = self._read_for_write(path)
            title = _list_title(original) or path.stem
            lines = original.split("\n")
            index = self._find_shopping_item(list_id, lines, target_line)
            dropped = lines.pop(index)
            match = _ITEM.match(dropped)
            body = match.group("body") if match else dropped
            entry = f"- dropped {today}: {body} (from {title})"

            path.write_text("\n".join(lines), encoding="utf-8")
            self._append_to_completed_log([entry], today)
            relpath = path.relative_to(self.root).as_posix()
            self._commit_and_push(
                [relpath, COMPLETED_LOG], f"alfred api: drop item from {relpath}"
            )

    def create_shopping_list(self, name: str) -> ShoppingListSummary:
        """Scaffold a new active shopping list under SHOPPING_DIR.

        `name` is kebab-cased into the filename; rejected if nothing
        kebab-able survives. 409s (ShoppingListExistsError) if the file
        already exists — never overwrites.
        """
        title = name.strip()
        slug = _kebab_case(title)
        if not slug:
            raise ValueError("name must contain at least one letter or digit")
        relpath = f"{SHOPPING_DIR}/{slug}.md"
        today = date.today().isoformat()

        with self._write_lock():
            self._pull()
            path = self.root / relpath
            if path.exists():
                raise ShoppingListExistsError(relpath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _SHOPPING_LIST_TEMPLATE.format(created=today, title=title), encoding="utf-8"
            )
            self._commit_and_push([relpath], f"alfred api: create shopping list '{title}'")
        return ShoppingListSummary(id=relpath, title=title, total=0, unticked=0)

    def sweep(self) -> SweepResult:
        """Sweep ticked items from the rolling to-do and every active shopping
        list, in one git transaction — one commit, or none if nothing was
        ticked anywhere.

        Rolling-todo sweep behaviour is unchanged: ticked lines move to
        COMPLETED_LOG, no capture file. Shopping additionally gets ONE
        0-inbox capture file, written only when at least one shopping item
        was swept, naming each item's source list — this is how playbook
        notes embedded in list files (e.g. "bought items go to the hardware
        inventory") get actioned later by a vault session; the sweep itself
        never interprets them.
        """
        today = date.today().isoformat()
        with self._write_lock():
            self._pull()

            todo_path = self.root / ROLLING_TODO
            todo_kept: list[str] = []
            todo_swept: list[str] = []
            for line in self._read_for_write(todo_path).split("\n"):
                match = _CHECKED_ITEM.match(line)
                if match:
                    todo_swept.append(match.group("body"))
                else:
                    todo_kept.append(line)

            shopping_swept: list[dict[str, str]] = []
            shopping_touched: list[str] = []
            completed_entries = [f"- completed {today}: {body}" for body in todo_swept]
            capture_lines: list[str] = []

            for summary in self._scan_shopping_lists():
                path = self.root / summary.id
                lines = self._read_for_write(path).split("\n")
                kept: list[str] = []
                bodies: list[str] = []
                for line in lines:
                    match = _ITEM.match(line)
                    if match and match.group("box").lower() == "x":
                        bodies.append(match.group("body"))
                    else:
                        kept.append(line)
                if not bodies:
                    continue
                path.write_text("\n".join(kept), encoding="utf-8")
                shopping_touched.append(summary.id)
                for body in bodies:
                    shopping_swept.append({"list": summary.title, "item": body})
                    completed_entries.append(f"- completed {today}: {body} (from {summary.title})")
                    capture_lines.append(f"- swept from {summary.title}: {body}")

            if not todo_swept and not shopping_swept:
                return SweepResult(todo_swept=[], shopping_swept=[])

            if todo_swept:
                todo_path.write_text("\n".join(todo_kept), encoding="utf-8")

            self._append_to_completed_log(completed_entries, today)
            touched = ([ROLLING_TODO] if todo_swept else []) + shopping_touched + [COMPLETED_LOG]

            if shopping_swept:
                touched.append(self._write_inbox_capture(capture_lines))

            count = len(todo_swept) + len(shopping_swept)
            self._commit_and_push(
                touched, f"alfred sweep: {count} item{'s' if count != 1 else ''} to completed-log"
            )
        return SweepResult(todo_swept=todo_swept, shopping_swept=shopping_swept)

    def _write_inbox_capture(self, lines: list[str]) -> str:
        """Write one 0-inbox capture file for a sweep's shopping items.

        No frontmatter, no headers — just bullets, so it reads as a quick
        capture note for the next vault session to triage.
        """
        relpath = f"{INBOX_DIR}/{datetime.now():%Y-%m-%d-%H%M}-shopping-sweep.md"
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return relpath

    def _append_to_completed_log(self, entries: list[str], today: str) -> None:
        """Append ledger entries, creating the log with frontmatter if absent."""
        log_path = self.root / COMPLETED_LOG
        if log_path.exists():
            log_text = self._read_for_write(log_path)
            if not log_text.endswith("\n"):
                log_text += "\n"
        else:
            log_text = _COMPLETED_LOG_TEMPLATE.format(created=today)
        log_path.write_text(log_text + "\n".join(entries) + "\n", encoding="utf-8")

    def _find_item(self, lines: list[str], target_line: str) -> int:
        """Index of the exact target line, which must be an unchecked item.

        Requiring the item shape means the API can only ever touch to-do lines,
        never frontmatter, headings, or prose, whatever a client sends.
        """
        if _UNCHECKED_ITEM.match(target_line):
            try:
                return lines.index(target_line)
            except ValueError:
                pass
        raise ItemNotFoundError(items=parse_unchecked_items("\n".join(lines)))

    def _read_for_write(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise VaultWriteError(f"cannot read {path.name}: {exc}") from exc

    @contextmanager
    def _write_lock(self):
        """Hold the flock shared with vault-sync.sh, or raise VaultBusyError.

        fcntl is imported here, not at module top, so the read-only paths still
        import on non-POSIX dev machines.
        """
        import fcntl

        deadline = time.monotonic() + WRITE_LOCK_TIMEOUT
        with open(self.root / WRITE_LOCK, "w") as lock_file:
            while True:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise VaultBusyError(
                            "vault is locked (sync in progress) — try again shortly"
                        ) from None
                    time.sleep(0.5)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _pull(self) -> None:
        # --ff-only matches vault-sync.sh: the Pi is a mirror of origin and a
        # write pushes immediately, so fast-forward always suffices. A failure
        # here leaves the tree untouched (no merge state to unwind).
        try:
            self._git("pull", "--ff-only")
        except _GitError as exc:
            raise VaultSyncError(f"could not pull vault before writing: {exc}") from exc

    def _commit_and_push(self, relpaths: list[str], message: str) -> None:
        try:
            self._git("add", "--", *relpaths)
            self._git(
                "-c", f"user.name={GIT_USER_NAME}",
                "-c", f"user.email={GIT_USER_EMAIL}",
                "commit", "-m", message,
            )
            self._git("push")
        except _GitError as exc:
            # Whatever failed, put the clone back on origin so the tree is
            # clean and the next sync fast-forwards. The write is reported as
            # failed; nothing is half-applied.
            try:
                self._git("reset", "--hard", "@{upstream}")
            except _GitError as reset_exc:
                log.error("could not restore vault after failed write: %s", reset_exc)
            raise VaultSyncError(f"could not push write to origin: {exc}") from exc

    def _git(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise _GitError(f"git {args[0]} timed out after {GIT_TIMEOUT}s") from exc
        if result.returncode != 0:
            detail = (result.stderr.strip() or result.stdout.strip()).replace("\n", " | ")
            raise _GitError(f"git {args[0]} failed: {detail}")
        return result.stdout


# Created on first drop if the log doesn't exist yet; frontmatter follows the
# vault's house style (see rolling-todo.md).
_COMPLETED_LOG_TEMPLATE = """\
---
tags: [type/log, life/admin]
status: active
created: {created}
---

# Completed Log

Lines removed from [[rolling-todo]]. `completed` entries were ticked and then
swept by the nightly Alfred sweep (or an on-demand "clear completed");
`dropped` entries were deleted via the Alfred API as no longer relevant.

---

"""

# Scaffold for CREATE LIST — matches the house style seen across
# 6-life/shopping/*.md: frontmatter, H1, a Format line, then an empty
# checklist section ready for _insertion_index to find on the first add.
_SHOPPING_LIST_TEMPLATE = """\
---
tags: [type/list, life/shopping]
status: active
created: {created}
---

# {title}

**Format:** `- [ ] item — notes, optional ~£cost, optional [[wikilinks]]`

---

"""
