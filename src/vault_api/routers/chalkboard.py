"""Chalkboard: the rolling to-do, as consumed by the MorningSync alarm app.

Reads return unchecked items; each carries its raw `line`, which is the key a
client echoes back to target a tick or a drop (dates are not unique, so the
exact line is the only unambiguous handle). Writes are git transactions — see
Vault's write methods for the discipline.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import get_vault
from ..vault import ItemNotFoundError, Vault

router = APIRouter(tags=["chalkboard"])


class AddRequest(BaseModel):
    """A new to-do. The capture date is always today, applied server-side."""

    text: str = Field(min_length=1, max_length=500)


class TargetRequest(BaseModel):
    """Targets one existing item by its raw line, as returned by GET /chalkboard."""

    line: str = Field(min_length=1)


def _items(vault: Vault) -> list[dict[str, str]]:
    return [item.to_json() for item in vault.rolling_todo_items()]


def _stale_list_404(exc: ItemNotFoundError) -> HTTPException:
    # The client most likely holds a stale list; hand back the current one so
    # it can refresh and retarget without a second round trip.
    return HTTPException(
        status_code=404,
        detail={
            "error": "item not found — the list may have changed",
            "items": [item.to_json() for item in exc.items],
        },
    )


@router.get("/chalkboard")
def get_chalkboard(vault: Vault = Depends(get_vault)) -> list[dict[str, str]]:
    """Unchecked items from 6-life/rolling-todo.md.

    Returns [] with a 200 if the vault or the file is unavailable — the alarm
    app should render an empty chalkboard, not an error.
    """
    return _items(vault)


@router.post("/chalkboard", status_code=201)
def add_item(body: AddRequest, vault: Vault = Depends(get_vault)) -> dict:
    """Append a new item, dated today."""
    text = body.text.strip()
    if not text or "\n" in body.text or "\r" in body.text:
        raise HTTPException(status_code=422, detail="text must be a non-empty single line")
    added = vault.add_item(text)
    return {"added": added.to_json(), "items": _items(vault)}


@router.post("/chalkboard/tick")
def tick_item(body: TargetRequest, vault: Vault = Depends(get_vault)) -> dict:
    """Mark the targeted item done ('- [ ]' -> '- [x]').

    The line stays in the doc, greyed out on renderers; the overnight sweep
    removes ticked lines later.
    """
    try:
        vault.tick_item(body.line)
    except ItemNotFoundError as exc:
        raise _stale_list_404(exc) from exc
    return {"ticked": body.line, "items": _items(vault)}


@router.post("/chalkboard/drop")
def drop_item(body: TargetRequest, vault: Vault = Depends(get_vault)) -> dict:
    """Remove the targeted item as no longer relevant (distinct from done).

    The line is appended to 6-life/completed-log.md marked DROPPED — nothing
    is ever silently destroyed.
    """
    try:
        vault.drop_item(body.line)
    except ItemNotFoundError as exc:
        raise _stale_list_404(exc) from exc
    return {"dropped": body.line, "items": _items(vault)}


@router.post("/chalkboard/sweep")
def sweep_ticked(vault: Vault = Depends(get_vault)) -> dict:
    """Clear completed: remove every ticked line, logging each as COMPLETED.

    The same code path the nightly cron triggers (scripts/sweep-todo.sh curls
    this endpoint); this backs a future UI "clear completed" button. Nothing
    ticked is a clean no-op — no commit is made.
    """
    swept = vault.sweep_ticked()
    return {"swept": swept, "count": len(swept), "items": _items(vault)}
