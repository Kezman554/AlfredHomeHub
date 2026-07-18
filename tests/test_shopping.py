"""Tests for the shopping-list family: parsing, discovery, and the write path.

Parsing/discovery tests are pure-stdlib or read-only Vault calls — no lock
needed. Write-path tests exercise the real git transaction (a local bare
"origin" + a working clone) but bypass Vault._write_lock, whose flock is
POSIX-only (fcntl) and unavailable on this dev machine — the transaction
logic under test (pull, surgical edit, commit, push, hard-reset on failure)
is unaffected by that swap; only the lock acquisition itself is skipped.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from vault_api.vault import (
    COMPLETED_LOG,
    ROLLING_TODO,
    ListNotFoundError,
    ShoppingItemNotFoundError,
    ShoppingListExistsError,
    Vault,
    _frontmatter,
    _insertion_index,
    _kebab_case,
    _list_title,
    parse_shopping_items,
)

# --- Fixture content ----------------------------------------------------------

ALFRED_TECH = """\
---
tags: [type/list, life/shopping, project/alfred]
status: active
created: 2026-05-25
---

# Alfred Tech — Rolling Buy List

Hardware for the Alfred ecosystem.

**Format:** `- [ ] item — notes, optional ~£cost (stage/priority)`

---

## Hardware

- [ ] Compact speaker (USB/3.5mm) — Alfred voice output ~£10-30 (Stage 2)
- [ ] Short HDMI cable — for touchscreen ~£5 (Stage 3)

## SSD Buying Notes

> **Resolved:** SSD storage no longer a buy item.

**Price watch:** NVMe SSD prices are inflated.

## Subscriptions / Services (activate when needed)

- [ ] Fish Audio API — pay-as-you-go, primary TTS (Stage 2)
- [ ] Claude Max — when affordable (Long-term)
"""

EMPTY_LIST = """\
---
tags: [type/list, life/shopping]
status: active
created: 2026-05-25
---

# Fashion — Rolling Buy List

Clothing, footwear, accessories.

**Format:** `- [ ] item — notes, optional ~£cost`

---
"""

COMPLETE_LIST = """\
---
tags: [life/family, life/shopping, type/plan]
status: complete
created: 2026-05-02
---

# Feast Shopping List

## Meat

| Item | Bought? |
| --- | --- |
| Lamb | Yes |
"""

NO_TAG_LIST = """\
---
tags: [type/list, life/admin]
status: active
created: 2026-04-08
---

# Rolling To-Do

- [ ] Something unrelated
"""


# --- Pure parsing --------------------------------------------------------------


def test_parse_shopping_items_reads_ticked_and_unticked():
    items = parse_shopping_items(ALFRED_TECH)
    assert [i.text for i in items] == [
        "Compact speaker (USB/3.5mm) — Alfred voice output ~£10-30 (Stage 2)",
        "Short HDMI cable — for touchscreen ~£5 (Stage 3)",
        "Fish Audio API — pay-as-you-go, primary TTS (Stage 2)",
        "Claude Max — when affordable (Long-term)",
    ]
    assert all(not i.ticked for i in items)


def test_parse_shopping_items_ignores_prose_and_tables():
    for item in parse_shopping_items(COMPLETE_LIST):
        assert "Lamb" not in item.text


def test_parse_shopping_items_ticked_state():
    text = "- [ ] not bought\n- [x] bought\n- [X] also bought\n"
    items = parse_shopping_items(text)
    assert [i.ticked for i in items] == [False, True, True]


def test_frontmatter_extracts_tags_and_status():
    tags, status = _frontmatter(ALFRED_TECH)
    assert tags == ["type/list", "life/shopping", "project/alfred"]
    assert status == "active"


def test_frontmatter_absent_returns_empty():
    assert _frontmatter("# No frontmatter here\n") == ([], None)


def test_list_title_from_h1():
    assert _list_title(ALFRED_TECH) == "Alfred Tech — Rolling Buy List"


def test_list_title_ignores_h2():
    assert _list_title("## Not the title\n\nprose\n") is None


def test_kebab_case():
    assert _kebab_case("Camping Gear!") == "camping-gear"
    assert _kebab_case("  Multiple   Spaces  ") == "multiple-spaces"
    assert _kebab_case("!!!") == ""
    assert _kebab_case("") == ""


# --- Insertion point ------------------------------------------------------------


def test_insertion_index_after_last_item_before_trailing_prose():
    lines = ALFRED_TECH.split("\n")
    idx = _insertion_index(lines)
    # Must land after "Claude Max" (the last item line) — which is also the
    # true end of file here, so this doubles as the "no trailing section"
    # case. The important property: it's after every existing item.
    last_item_idx = max(i for i, l in enumerate(lines) if l.startswith("- ["))
    assert idx == last_item_idx + 1


def test_insertion_index_never_lands_inside_a_middle_prose_section():
    # A list whose items are followed by a prose section which is in turn
    # followed by more items further down — the insertion point (after the
    # LAST item) must never fall inside the middle prose section.
    text = (
        "- [ ] first\n\n## Notes\nsome prose\n\n## More\n- [ ] second\n"
    )
    lines = text.split("\n")
    idx = _insertion_index(lines)
    assert lines[idx - 1] == "- [ ] second"


def test_insertion_index_empty_list_lands_before_next_section():
    text = "**Format:** `- [ ] item`\n\n---\n\n## Bought\n\n| a | b |\n"
    lines = text.split("\n")
    idx = _insertion_index(lines)
    assert lines[idx] == "## Bought"


def test_insertion_index_empty_list_no_following_section_is_near_eof():
    lines = EMPTY_LIST.split("\n")
    idx = _insertion_index(lines)
    new_lines = lines[:idx] + ["- [ ] new item"] + lines[idx:]
    rebuilt = "\n".join(new_lines)
    assert "- [ ] new item" in rebuilt
    # No blank line introduced immediately before the new item's line, and
    # the file still ends cleanly (no doubled trailing newline artifact).
    assert rebuilt.count("- [ ] new item\n\n\n") == 0


# --- Discovery / read (no write lock involved) ----------------------------------


def write(root: Path, relpath: str, text: str) -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discovery_returns_only_active_shopping_tagged_lists(tmp_path):
    write(tmp_path, "6-life/shopping/alfred-tech.md", ALFRED_TECH)
    write(tmp_path, "6-life/shopping/fashion.md", EMPTY_LIST)
    write(tmp_path, "6-life/jess-birthday-2026/feast-shopping-list.md", COMPLETE_LIST)
    write(tmp_path, "6-life/rolling-todo.md", NO_TAG_LIST)

    lists = Vault(root=tmp_path).shopping_lists()
    ids = {s.id for s in lists}
    assert ids == {"6-life/shopping/alfred-tech.md", "6-life/shopping/fashion.md"}


def test_discovery_finds_lists_wherever_they_live(tmp_path):
    # Not under 6-life/shopping/ at all — discovery is by tag+status, not path.
    write(tmp_path, "elsewhere/deep/custom-list.md", ALFRED_TECH)
    lists = Vault(root=tmp_path).shopping_lists()
    assert [s.id for s in lists] == ["elsewhere/deep/custom-list.md"]


def test_discovery_counts_and_title(tmp_path):
    write(tmp_path, "6-life/shopping/alfred-tech.md", ALFRED_TECH)
    lists = Vault(root=tmp_path).shopping_lists()
    summary = lists[0]
    assert summary.title == "Alfred Tech — Rolling Buy List"
    assert summary.total == 4
    assert summary.unticked == 4


def test_discovery_title_falls_back_to_filename(tmp_path):
    no_h1 = "---\ntags: [type/list, life/shopping]\nstatus: active\ncreated: 2026-01-01\n---\n\n- [ ] item\n"
    write(tmp_path, "6-life/shopping/no-title.md", no_h1)
    lists = Vault(root=tmp_path).shopping_lists()
    assert lists[0].title == "no-title"


def test_shopping_list_items_reads_ticked_included(tmp_path):
    write(tmp_path, "6-life/shopping/alfred-tech.md", ALFRED_TECH)
    summary, items = Vault(root=tmp_path).shopping_list_items("6-life/shopping/alfred-tech.md")
    assert summary.id == "6-life/shopping/alfred-tech.md"
    assert len(items) == 4


def test_shopping_list_items_rejects_completed_list(tmp_path):
    write(tmp_path, "6-life/jess-birthday-2026/feast-shopping-list.md", COMPLETE_LIST)
    with pytest.raises(ListNotFoundError):
        Vault(root=tmp_path).shopping_list_items("6-life/jess-birthday-2026/feast-shopping-list.md")


def test_shopping_list_items_missing_file_raises_with_current_lists(tmp_path):
    write(tmp_path, "6-life/shopping/alfred-tech.md", ALFRED_TECH)
    with pytest.raises(ListNotFoundError) as exc_info:
        Vault(root=tmp_path).shopping_list_items("6-life/shopping/nonexistent.md")
    assert [s.id for s in exc_info.value.lists] == ["6-life/shopping/alfred-tech.md"]


def test_shopping_list_items_rejects_path_traversal(tmp_path):
    (tmp_path / "secret.txt").write_text("hunter2", encoding="utf-8")
    with pytest.raises(ListNotFoundError):
        Vault(root=tmp_path).shopping_list_items("../secret.txt")
    with pytest.raises(ListNotFoundError):
        Vault(root=tmp_path).shopping_list_items("6-life/shopping/../../../secret.txt")


# --- Write path: real git transaction, lock bypassed ----------------------------


@contextmanager
def _no_lock():
    yield


def make_repo(tmp_path: Path) -> Path:
    """A bare 'origin' plus a working clone seeded with vault fixture content.

    Mirrors the Pi's setup closely enough to exercise Vault's real pull ->
    edit -> commit -> push -> (hard-reset on failure) transaction.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "seed@test"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "Seed"], check=True)

    write(seed, "6-life/rolling-todo.md", NO_TAG_LIST)
    write(seed, "6-life/shopping/alfred-tech.md", ALFRED_TECH)
    write(seed, "6-life/shopping/fashion.md", EMPTY_LIST)
    write(seed, "6-life/jess-birthday-2026/feast-shopping-list.md", COMPLETE_LIST)

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
    subprocess.run(
        ["git", "clone", str(origin), str(clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "seed@test"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "Seed"], check=True)
    # The bare origin's HEAD may still point at whatever init.defaultBranch
    # was (e.g. master) even though only "main" was ever pushed to it — force
    # the clone onto main, tracking origin/main, regardless of clone's guess.
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-B", "main", "origin/main"],
        check=True,
        capture_output=True,
    )
    return clone


def make_vault(tmp_path: Path, monkeypatch) -> Vault:
    clone = make_repo(tmp_path)
    vault = Vault(root=clone)
    monkeypatch.setattr(Vault, "_write_lock", lambda self: _no_lock())
    return vault


def git_log(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "log", "--oneline"], check=True, capture_output=True, text=True
    )
    return result.stdout


def test_add_shopping_item_lands_after_last_item_untouched_elsewhere(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    before = (vault.root / "6-life/shopping/alfred-tech.md").read_text(encoding="utf-8")

    added = vault.add_shopping_item("6-life/shopping/alfred-tech.md", "New gadget ~£20")
    assert added.text == "New gadget ~£20"
    assert added.ticked is False

    after = (vault.root / "6-life/shopping/alfred-tech.md").read_text(encoding="utf-8")
    assert after != before
    assert "- [ ] New gadget ~£20" in after
    # Only the new line was introduced — every other line survives untouched.
    before_lines = set(before.splitlines())
    after_lines = set(after.splitlines())
    assert after_lines - before_lines == {"- [ ] New gadget ~£20"}
    assert before_lines - after_lines == set()

    assert "alfred api: add" in git_log(vault.root)


def test_add_tick_drop_cycle_leaves_rest_of_file_byte_identical(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    list_id = "6-life/shopping/alfred-tech.md"
    baseline = (vault.root / list_id).read_text(encoding="utf-8")

    added = vault.add_shopping_item(list_id, "Cycle test item")
    vault.tick_shopping_item(list_id, added.line)
    ticked_line = added.line.replace("[ ]", "[x]", 1)
    vault.drop_shopping_item(list_id, ticked_line)

    final = (vault.root / list_id).read_text(encoding="utf-8")
    assert final == baseline


def test_tick_then_drop_logs_to_completed_log_naming_the_list(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    list_id = "6-life/shopping/fashion.md"
    added = vault.add_shopping_item(list_id, "Winter coat")
    vault.drop_shopping_item(list_id, added.line)

    log_text = (vault.root / COMPLETED_LOG).read_text(encoding="utf-8")
    assert "dropped" in log_text
    assert "Winter coat" in log_text
    assert "Fashion — Rolling Buy List" in log_text


def test_tick_stale_line_raises_with_current_items(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    with pytest.raises(ShoppingItemNotFoundError) as exc_info:
        vault.tick_shopping_item("6-life/shopping/alfred-tech.md", "- [ ] does not exist")
    assert len(exc_info.value.items) == 4


def test_tick_already_ticked_item_is_stale(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    list_id = "6-life/shopping/alfred-tech.md"
    added = vault.add_shopping_item(list_id, "Toggle test")
    vault.tick_shopping_item(list_id, added.line)
    ticked_line = added.line.replace("[ ]", "[x]", 1)
    with pytest.raises(ShoppingItemNotFoundError):
        vault.tick_shopping_item(list_id, ticked_line)


def test_add_to_unknown_list_raises_list_not_found(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    with pytest.raises(ListNotFoundError):
        vault.add_shopping_item("6-life/shopping/does-not-exist.md", "item")


def test_add_to_completed_list_raises_list_not_found(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    with pytest.raises(ListNotFoundError):
        vault.add_shopping_item(
            "6-life/jess-birthday-2026/feast-shopping-list.md", "should not land"
        )


def test_create_shopping_list_scaffolds_and_appears_in_discovery(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    created = vault.create_shopping_list("Camping Gear!")
    assert created.id == "6-life/shopping/camping-gear.md"
    assert created.title == "Camping Gear!"

    ids = {s.id for s in vault.shopping_lists()}
    assert created.id in ids

    text = (vault.root / created.id).read_text(encoding="utf-8")
    assert "life/shopping" in text
    assert "status: active" in text
    assert "# Camping Gear!" in text


def test_create_shopping_list_then_add_lands_cleanly(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    created = vault.create_shopping_list("Garden")
    added = vault.add_shopping_item(created.id, "Trowel")
    _, items = vault.shopping_list_items(created.id)
    assert [i.text for i in items] == ["Trowel"]
    assert added.line in (vault.root / created.id).read_text(encoding="utf-8")


def test_create_shopping_list_rejects_empty_name(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        vault.create_shopping_list("!!!")


def test_create_shopping_list_conflict_is_409ish(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    vault.create_shopping_list("Garden")
    with pytest.raises(ShoppingListExistsError):
        vault.create_shopping_list("Garden")


def test_sweep_shopping_and_todo_together_one_commit(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    list_id = "6-life/shopping/alfred-tech.md"
    added = vault.add_shopping_item(list_id, "Sweep me")
    vault.tick_shopping_item(list_id, added.line)

    log_before = git_log(vault.root)
    commits_before = len(log_before.splitlines())

    result = vault.sweep()
    assert result.todo_swept == []  # NO_TAG_LIST has no ticked rolling-todo items
    assert result.shopping_swept == [{"list": "Alfred Tech — Rolling Buy List", "item": "Sweep me"}]

    log_after = git_log(vault.root)
    commits_after = len(log_after.splitlines())
    assert commits_after == commits_before + 1  # one commit for the whole sweep

    _, items = vault.shopping_list_items(list_id)
    assert "Sweep me" not in [i.text for i in items]

    inbox = sorted((vault.root / "0-inbox").glob("*-shopping-sweep.md"))
    assert len(inbox) == 1
    capture_text = inbox[0].read_text(encoding="utf-8")
    assert "swept from Alfred Tech — Rolling Buy List: Sweep me" in capture_text

    completed = (vault.root / COMPLETED_LOG).read_text(encoding="utf-8")
    assert "Sweep me" in completed


def test_sweep_with_nothing_ticked_is_a_clean_noop(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    commits_before = len(git_log(vault.root).splitlines())

    result = vault.sweep()
    assert result.todo_swept == []
    assert result.shopping_swept == []

    commits_after = len(git_log(vault.root).splitlines())
    assert commits_after == commits_before
    assert not list((vault.root / "0-inbox").glob("*")) if (vault.root / "0-inbox").exists() else True


def test_sweep_todo_only_produces_no_inbox_capture(tmp_path, monkeypatch):
    vault = make_vault(tmp_path, monkeypatch)
    todo_text = (vault.root / ROLLING_TODO).read_text(encoding="utf-8")
    todo_text += "- [x] ticked todo item\n"
    (vault.root / ROLLING_TODO).write_text(todo_text, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(vault.root), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(vault.root), "commit", "-m", "prep"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(vault.root), "push"], check=True, capture_output=True
    )

    result = vault.sweep()
    assert result.todo_swept == ["ticked todo item"]
    assert result.shopping_swept == []

    inbox_dir = vault.root / "0-inbox"
    assert not inbox_dir.exists() or not list(inbox_dir.glob("*"))


# --- HTTP layer: routing (the greedy {list_id:path} ordering) -------------------


def test_http_add_tick_drop_route_correctly_despite_greedy_path_param(tmp_path, monkeypatch):
    # {list_id:path} is a greedy match; /tick and /drop must be registered
    # ahead of the plain add route in shopping.py or a POST to
    # ".../fitness.md/tick" would be swallowed by add instead of reaching
    # tick_shopping_item. This drives the real FastAPI app end-to-end to
    # prove the routes resolve to the right handlers, not just that the
    # underlying Vault methods work.
    from fastapi.testclient import TestClient

    from vault_api.app import app
    from vault_api.dependencies import get_vault

    vault = make_vault(tmp_path, monkeypatch)
    app.dependency_overrides[get_vault] = lambda: vault
    client = TestClient(app)
    try:
        list_id = "6-life/shopping/fashion.md"

        r = client.get("/shopping")
        assert r.status_code == 200
        assert {s["id"] for s in r.json()} >= {list_id}

        r = client.post(f"/shopping/{list_id}", json={"text": "Winter coat"})
        assert r.status_code == 201
        line = r.json()["added"]["line"]
        assert line == "- [ ] Winter coat"

        r = client.post(f"/shopping/{list_id}/tick", json={"line": line})
        assert r.status_code == 200
        assert r.json()["ticked"] == line
        ticked_line = line.replace("[ ]", "[x]", 1)
        assert any(i["line"] == ticked_line for i in r.json()["items"])

        r = client.post(f"/shopping/{list_id}/drop", json={"line": ticked_line})
        assert r.status_code == 200
        assert r.json()["dropped"] == ticked_line
        assert not any(i["text"] == "Winter coat" for i in r.json()["items"])

        r = client.get(f"/shopping/{list_id}")
        assert r.status_code == 200
        assert r.json()["items"] == []
    finally:
        app.dependency_overrides.pop(get_vault, None)


def test_http_stale_line_returns_404_with_current_items(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vault_api.app import app
    from vault_api.dependencies import get_vault

    vault = make_vault(tmp_path, monkeypatch)
    app.dependency_overrides[get_vault] = lambda: vault
    client = TestClient(app)
    try:
        r = client.post(
            "/shopping/6-life/shopping/alfred-tech.md/tick",
            json={"line": "- [ ] does not exist"},
        )
        assert r.status_code == 404
        assert "items" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_vault, None)


def test_http_unknown_list_returns_404_with_current_lists(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vault_api.app import app
    from vault_api.dependencies import get_vault

    vault = make_vault(tmp_path, monkeypatch)
    app.dependency_overrides[get_vault] = lambda: vault
    client = TestClient(app)
    try:
        r = client.get("/shopping/6-life/shopping/does-not-exist.md")
        assert r.status_code == 404
        assert "lists" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_vault, None)


def test_http_create_list_conflict_is_409(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from vault_api.app import app
    from vault_api.dependencies import get_vault

    vault = make_vault(tmp_path, monkeypatch)
    app.dependency_overrides[get_vault] = lambda: vault
    client = TestClient(app)
    try:
        r = client.post("/shopping", json={"name": "Alfred Tech"})
        assert r.status_code == 409

        r = client.post("/shopping", json={"name": "!!!"})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_vault, None)
