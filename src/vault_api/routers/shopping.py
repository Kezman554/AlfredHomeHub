"""Shopping lists: a discovered family, not one hardcoded file.

Any vault .md tagged life/shopping + status: active is a shopping list,
wherever it lives — 6-life/shopping/ is just where CREATE LIST puts new ones.
Reads return every item (ticked or not); each carries its raw `line`, the key
write endpoints echo back to target a tick or a drop — same convention as the
chalkboard, generalised to (list, line) instead of just (line). Writes are git
transactions — see Vault's write methods for the discipline.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import get_vault
from ..vault import (
    ListNotFoundError,
    ShoppingItemNotFoundError,
    ShoppingListExistsError,
    Vault,
)

router = APIRouter(tags=["shopping"])


class AddShoppingItemRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ShoppingTargetRequest(BaseModel):
    """Targets one existing item by its raw line, as returned by GET /shopping/{list_id}."""

    line: str = Field(min_length=1)


class CreateShoppingListRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _stale_lists_404(exc: ListNotFoundError) -> HTTPException:
    # The client likely holds a stale/renamed list id; hand back current
    # discovery so it can refresh and retarget without a second round trip.
    return HTTPException(
        status_code=404,
        detail={
            "error": "shopping list not found — it may have been renamed, completed, or removed",
            "lists": [summary.to_json() for summary in exc.lists],
        },
    )


def _stale_items_404(exc: ShoppingItemNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": "item not found — the list may have changed",
            "list_id": exc.list_id,
            "items": [item.to_json() for item in exc.items],
        },
    )


@router.get("/shopping")
def get_shopping_lists(vault: Vault = Depends(get_vault)) -> list[dict]:
    """Every active shopping list in the vault, wherever it lives."""
    return [summary.to_json() for summary in vault.shopping_lists()]


@router.post("/shopping", status_code=201)
def create_shopping_list(body: CreateShoppingListRequest, vault: Vault = Depends(get_vault)) -> dict:
    """Scaffold a new active shopping list under 6-life/shopping/."""
    try:
        created = vault.create_shopping_list(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ShoppingListExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return created.to_json()


# Registration order matters: {list_id:path} is a greedy match, so /sweep, /tick
# and /drop must be registered before the plain add route or they'd never be
# reached — a POST to "/shopping/sweep" would otherwise match the add route with
# list_id="sweep", and ".../fitness.md/tick" with list_id="...fitness.md/tick".


@router.post("/shopping/sweep")
def sweep_shopping(vault: Vault = Depends(get_vault)) -> dict:
    """Clear bought items from every active shopping list: remove each '- [x]'
    line, log it BOUGHT in the completed-log, and write ONE inbox capture naming
    what was bought from which list (the handoff for a vault session to file it
    into the right inventory).

    The shopping-only counterpart to POST /chalkboard/sweep (which sweeps the
    to-do and shopping together) — same code path, so they can't diverge. Backs
    a future "clear bought" button; the nightly 3am job already sweeps shopping
    via the combined endpoint. Nothing bought anywhere is a clean no-op.
    """
    result = vault.sweep_shopping()
    return {
        "shopping_swept": result.shopping_swept,
        "shopping_count": len(result.shopping_swept),
    }


@router.post("/shopping/{list_id:path}/tick")
def tick_shopping_item(
    list_id: str, body: ShoppingTargetRequest, vault: Vault = Depends(get_vault)
) -> dict:
    """Mark the targeted item bought ('- [ ]' -> '- [x]'). It stays in the doc,
    struck through by renderers, until a sweep (POST /shopping/sweep, or the
    nightly combined job) removes it and logs it BOUGHT to the completed-log."""
    try:
        vault.tick_shopping_item(list_id, body.line)
    except ListNotFoundError as exc:
        raise _stale_lists_404(exc) from exc
    except ShoppingItemNotFoundError as exc:
        raise _stale_items_404(exc) from exc
    summary, items = vault.shopping_list_items(list_id)
    return {"ticked": body.line, "list": summary.to_json(), "items": [i.to_json() for i in items]}


@router.post("/shopping/{list_id:path}/drop")
def drop_shopping_item(
    list_id: str, body: ShoppingTargetRequest, vault: Vault = Depends(get_vault)
) -> dict:
    """Remove the targeted item as no longer wanted (distinct from bought).

    Appended to 6-life/completed-log.md marked DROPPED, naming the source
    list — nothing is ever silently destroyed.
    """
    try:
        vault.drop_shopping_item(list_id, body.line)
    except ListNotFoundError as exc:
        raise _stale_lists_404(exc) from exc
    except ShoppingItemNotFoundError as exc:
        raise _stale_items_404(exc) from exc
    summary, items = vault.shopping_list_items(list_id)
    return {"dropped": body.line, "list": summary.to_json(), "items": [i.to_json() for i in items]}


@router.post("/shopping/{list_id:path}", status_code=201)
def add_shopping_item(
    list_id: str, body: AddShoppingItemRequest, vault: Vault = Depends(get_vault)
) -> dict:
    """Append a new unticked item to the targeted list."""
    text = body.text.strip()
    if not text or "\n" in body.text or "\r" in body.text:
        raise HTTPException(status_code=422, detail="text must be a non-empty single line")
    try:
        added = vault.add_shopping_item(list_id, text)
    except ListNotFoundError as exc:
        raise _stale_lists_404(exc) from exc
    summary, items = vault.shopping_list_items(list_id)
    return {"added": added.to_json(), "list": summary.to_json(), "items": [i.to_json() for i in items]}


@router.get("/shopping/{list_id:path}")
def get_shopping_list(list_id: str, vault: Vault = Depends(get_vault)) -> dict:
    """One list's items, ticked included — each carries its raw `line` key."""
    try:
        summary, items = vault.shopping_list_items(list_id)
    except ListNotFoundError as exc:
        raise _stale_lists_404(exc) from exc
    return {"list": summary.to_json(), "items": [i.to_json() for i in items]}
