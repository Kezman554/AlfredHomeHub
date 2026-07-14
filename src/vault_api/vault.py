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
from pathlib import Path

log = logging.getLogger(__name__)

# Relative to the vault root. Kept here rather than inline in a router so other
# note types (daily schedule, family calendar) can be added alongside it.
ROLLING_TODO = "6-life/rolling-todo.md"

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
