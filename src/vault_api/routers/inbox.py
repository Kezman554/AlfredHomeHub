"""Inbox: plain capture into 0-inbox/.

Text in, one file out, named YYYY-MM-DD-HHMM-slug.md with the raw text as its
entire content — no frontmatter, no headers, matching the vault's inbox
convention (a few bullets or plain prose, ephemeral).

Deliberately narrow: no tag parsing or routing (#todo, #idea), no dedupe or
merge (every POST is a new file), and no delete/edit/triage. Inbox notes are
consumed by triaging them in a vault session, never through this API. Writes
are git transactions — see Vault's write methods for the discipline.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from ..dependencies import get_vault
from ..vault import Vault

router = APIRouter(tags=["inbox"])

# Generous next to the to-do's 500: a capture is whatever was on your mind,
# possibly several lines of it, but still a note rather than a document.
MAX_CAPTURE = 10_000


@router.get("/inbox")
def get_inbox(vault: Vault = Depends(get_vault)) -> list[dict[str, str]]:
    """Current 0-inbox/ files as [{filename, content}], newest first.

    Returns [] with a 200 if the vault or the directory is unavailable, same
    as the other reads — an empty inbox and an unreachable one look alike to
    a client, and neither is worth a 500.
    """
    return [note.to_json() for note in vault.inbox_notes()]


@router.post("/inbox", status_code=201)
def capture(
    text: str = Body(media_type="text/plain"), vault: Vault = Depends(get_vault)
) -> dict:
    """Capture plain text as a new inbox file.

    The body is the text itself (text/plain), not JSON: captures come from
    voice and shortcuts, where wrapping a sentence in JSON is pure friction.
    """
    if not text.strip():
        raise HTTPException(status_code=422, detail="capture text must not be empty")
    if len(text) > MAX_CAPTURE:
        raise HTTPException(
            status_code=422, detail=f"capture text must be at most {MAX_CAPTURE} characters"
        )
    return {"captured": vault.capture_to_inbox(text).to_json()}
