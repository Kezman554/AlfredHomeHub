"""Tests for inbox capture: the slug helper, the write transaction, and reads.

Same arrangement as test_shopping.py — a local bare "origin" plus a working
clone exercise the real pull -> write -> commit -> push transaction, with
Vault._write_lock bypassed because its flock is POSIX-only (fcntl) and this
dev machine is Windows. Only lock acquisition is skipped; the transaction
under test is the real one.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from vault_api.vault import INBOX_DIR, InboxNote, Vault, _inbox_slug


# --- Slugging ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Book the car in for its MOT", "book-the-car-in-for-its"),  # capped at 6 words
        ("Idea", "idea"),
        ("  leading and trailing  ", "leading-and-trailing"),
        ("Call Jess re: the boiler!", "call-jess-re-the-boiler"),
        ("multi\nline capture here", "multi-line-capture-here"),
        ("£20 for the thing", "20-for-the-thing"),
    ],
)
def test_slug_from_first_words(text, expected):
    assert _inbox_slug(text) == expected


def test_slug_falls_back_when_nothing_kebab_able():
    # The timestamp still makes the filename unique, so this is a valid name.
    assert _inbox_slug("!!! ??? ***") == "note"


def test_slug_is_length_capped():
    slug = _inbox_slug("supercalifragilistic " * 6)
    assert len(slug) <= 48
    assert not slug.endswith("-")


# --- Fixture repo -----------------------------------------------------------


@contextmanager
def _no_lock():
    yield


def make_vault(tmp_path: Path, monkeypatch) -> Vault:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "seed@test"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "Seed"], check=True)
    (seed / "README.md").write_text("vault\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-m", "seed"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(seed), "branch", "-M", "main"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", str(origin), "main"], check=True, capture_output=True
    )

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "seed@test"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "Seed"], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-B", "main", "origin/main"],
        check=True,
        capture_output=True,
    )

    monkeypatch.setattr(Vault, "_write_lock", lambda self: _no_lock())
    return Vault(root=clone)


def git_log(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%s|%an|%ae"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


# --- Capture ----------------------------------------------------------------


def test_capture_writes_raw_text_with_no_frontmatter(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    note = vault.capture_to_inbox("- ring the dentist\n- book MOT")

    path = vault.root / INBOX_DIR / note.filename
    content = path.read_text(encoding="utf-8")
    assert content == "- ring the dentist\n- book MOT\n"
    assert not content.startswith("---")
    assert "#" not in content


def test_capture_filename_shape(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    note = vault.capture_to_inbox("Ring the dentist about Oliver")

    # YYYY-MM-DD-HHMM-slug.md
    assert note.filename.endswith("-ring-the-dentist-about-oliver.md")
    stamp = note.filename.split("-ring")[0]
    assert len(stamp) == len("2026-07-20-1432")


def test_capture_commit_message_and_author(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    vault.capture_to_inbox("Look into the loft insulation grant")

    head = git_log(vault.root).splitlines()[0]
    assert head == (
        "alfred api: inbox capture 'look-into-the-loft-insulation-grant'"
        "|Alfred|alfred@alfred.local"
    )


def test_capture_is_pushed_to_origin(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    note = vault.capture_to_inbox("Push me to origin")

    remote = subprocess.run(
        ["git", "-C", str(vault.root), "ls-tree", "-r", "--name-only", "origin/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"{INBOX_DIR}/{note.filename}" in remote


def test_capture_leaves_clean_tree(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    vault.capture_to_inbox("Nothing should be left staged")

    status = subprocess.run(
        ["git", "-C", str(vault.root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""


def test_every_capture_is_a_new_file_even_when_identical(tmp_path, monkeypatch):
    # No dedupe or merge: two identical captures are two notes, and neither
    # overwrites the other even within the same minute.
    vault = make_vault(tmp_path, monkeypatch)
    first = vault.capture_to_inbox("same text")
    second = vault.capture_to_inbox("same text")

    assert first.filename != second.filename
    assert len(list((vault.root / INBOX_DIR).glob("*.md"))) == 2


def test_capture_normalises_crlf(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    note = vault.capture_to_inbox("line one\r\nline two\r\n")

    content = (vault.root / INBOX_DIR / note.filename).read_text(encoding="utf-8")
    assert content == "line one\nline two\n"


def test_capture_rejects_empty_text(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        vault.capture_to_inbox("   \n  ")


# --- Listing ----------------------------------------------------------------


def test_inbox_notes_lists_filename_and_content_newest_first(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    inbox = vault.root / INBOX_DIR
    inbox.mkdir(parents=True)
    (inbox / "2026-07-18-0900-older.md").write_text("older note\n", encoding="utf-8")
    (inbox / "2026-07-20-1432-newer.md").write_text("newer note\n", encoding="utf-8")

    notes = vault.inbox_notes()
    assert notes == [
        InboxNote(filename="2026-07-20-1432-newer.md", content="newer note\n"),
        InboxNote(filename="2026-07-18-0900-older.md", content="older note\n"),
    ]


def test_inbox_notes_ignores_non_markdown(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    inbox = vault.root / INBOX_DIR
    inbox.mkdir(parents=True)
    (inbox / "2026-07-20-1432-real.md").write_text("keep\n", encoding="utf-8")
    (inbox / "photo.png").write_bytes(b"\x89PNG")

    assert [note.filename for note in vault.inbox_notes()] == ["2026-07-20-1432-real.md"]


def test_inbox_notes_empty_when_directory_absent(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    assert vault.inbox_notes() == []


def test_captured_note_appears_in_listing(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    note = vault.capture_to_inbox("round trip me")

    notes = vault.inbox_notes()
    assert note in notes
    assert notes[0].content == "round trip me\n"


def test_note_to_json_shape(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    vault.capture_to_inbox("shape check")
    payload = vault.inbox_notes()[0].to_json()
    assert set(payload) == {"filename", "content"}


# --- Over HTTP --------------------------------------------------------------
#
# The body is text/plain, not JSON — worth proving through the real app, since
# a wrong media_type would silently 422 every capture.


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vault_api import dependencies
    from vault_api.app import app

    vault = make_vault(tmp_path, monkeypatch)
    app.dependency_overrides[dependencies.get_vault] = lambda: vault
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_http_capture_then_list_round_trip(client):
    posted = client.post(
        "/inbox",
        content="ring the dentist about Oliver\nand book the MOT",
        headers={"Content-Type": "text/plain"},
    )
    assert posted.status_code == 201
    filename = posted.json()["captured"]["filename"]

    listed = client.get("/inbox")
    assert listed.status_code == 200
    assert listed.json() == [
        {"filename": filename, "content": "ring the dentist about Oliver\nand book the MOT\n"}
    ]


def test_http_empty_capture_is_422(client):
    response = client.post("/inbox", content="   ", headers={"Content-Type": "text/plain"})
    assert response.status_code == 422


def test_http_oversized_capture_is_422(client):
    response = client.post("/inbox", content="x" * 10_001, headers={"Content-Type": "text/plain"})
    assert response.status_code == 422


def test_http_empty_inbox_is_empty_list(client):
    assert client.get("/inbox").json() == []
