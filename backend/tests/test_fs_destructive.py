"""`_safe_resolve` as a DESTRUCTIVE-capability gate, not a read confinement.

`test_fs_confinement.py` proves the escape suite against `/fs/read`. That is a
proof about DISCLOSURE: get it wrong and a file leaks. `/fs/entry` (delete) and
`/fs/rename` put the same function behind operations where the identical bug
destroys someone's work permanently — there is no trash, no git and no backup
anywhere in this product — or moves a file into a different account's workspace,
which is a sibling directory on the same volume.

Passing the escape suite for `read` does not imply passing it for `unlink`, so
the whole suite is re-run here against both new verbs rather than assumed. That
is the entire reason this file exists separately.

Rename gets extra attention for one reason: it is the only route that takes TWO
caller-supplied paths. Validating just the source would leave an arbitrary WRITE
primitive — `to` would move the file anywhere the process can reach.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

ACCT = "00000000-0000-0000-0000-00000000000f"    # SINGLE_TENANT
OTHER = "11111111-2222-3333-4444-555555555555"   # a second account's workspace


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)

    import app.paths, app.fs, app.auth, app.main
    importlib.reload(app.paths)
    fs = importlib.reload(app.fs)
    importlib.reload(app.auth)
    main = importlib.reload(app.main)
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {
        "/fs/tree", "/fs/read", "/fs/write", "/fs/entry", "/fs/entry-info", "/fs/rename",
    })

    ws = fs.workspace_dir(ACCT)
    (ws / "keep.py").write_text("print('keep')\n", encoding="utf-8")
    (ws / "sub").mkdir(exist_ok=True)
    (ws / "sub" / "inner.txt").write_text("inner\n", encoding="utf-8")

    # A SECOND account's workspace, a sibling directory on the same volume.
    other = fs.workspace_dir(OTHER)
    (other / "theirs.py").write_text("SECRET-OTHER-ACCOUNT\n", encoding="utf-8")

    # And something outside the workspace root entirely.
    secret = tmp_path / "outside"
    secret.mkdir(parents=True, exist_ok=True)
    (secret / ".env").write_text("OPENROUTER_API_KEY=sk-or-CANARY\n", encoding="utf-8")

    return TestClient(main.app), fs, ws, other, secret


# ------------------------------------------------------------- delete ------

ESCAPES = [
    "../outside/.env",
    "../../outside/.env",
    "./../outside/.env",
    "sub/../../outside/.env",
]


@pytest.mark.parametrize("attack", ESCAPES)
def test_delete_cannot_climb_out(client, attack):
    """The read suite, re-run against unlink. Same guard, far worse failure."""
    c, _, _, _, secret = client
    r = c.delete("/fs/entry", params={"path": attack})
    assert r.status_code == 403, f"{attack} was not refused: {r.status_code}"
    assert (secret / ".env").exists(), "the file outside the workspace was DELETED"


def test_delete_cannot_reach_another_account(client):
    """The workspaces are siblings, so `../<other-uuid>/theirs.py` resolves to a
    real file that is not ours. Containment is per-ACCOUNT, not per-volume."""
    c, _, _, other, _ = client
    r = c.delete("/fs/entry", params={"path": f"../{OTHER}/theirs.py"})
    assert r.status_code == 403
    assert (other / "theirs.py").exists(), "another account's file was deleted"


def test_the_workspace_itself_cannot_be_deleted(client):
    """The root passes every containment check — it IS the boundary — so it
    needs its own refusal or one request wipes the whole account.

    Two different refusals, for two different reasons, and both are correct:
      "" and "." resolve TO the workspace root, so the explicit root guard
          catches them -> 400;
      "/" resolves to the filesystem root, which is OUTSIDE the workspace, so
          _safe_resolve refuses it one layer earlier -> 403.
    The assertion is on the outcome that matters — the workspace is still there
    — with the status pinned per case so a future change that collapses them
    into one path has to be deliberate.
    """
    c, _, ws, _, _ = client
    assert c.delete("/fs/entry", params={"path": "", "recursive": True}).status_code == 400
    assert c.delete("/fs/entry", params={"path": ".", "recursive": True}).status_code == 400
    assert c.delete("/fs/entry", params={"path": "/", "recursive": True}).status_code == 403
    assert ws.exists() and (ws / "keep.py").exists()


def test_deleting_a_file_works(client):
    c, _, ws, _, _ = client
    r = c.delete("/fs/entry", params={"path": "keep.py"})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1
    assert not (ws / "keep.py").exists()


def test_deleting_a_missing_path_is_404_not_a_silent_success(client):
    """Reporting "done" about something that was not there hides a stale view of
    the tree — and the next delete from that same stale view may hit the wrong
    row."""
    c, _, _, _, _ = client
    assert c.delete("/fs/entry", params={"path": "never-existed.txt"}).status_code == 404


def test_a_non_empty_directory_is_refused_without_recursive(client):
    c, _, ws, _, _ = client
    r = c.delete("/fs/entry", params={"path": "sub"})
    assert r.status_code == 409
    assert r.json()["detail"]["entries"] == 1
    assert (ws / "sub" / "inner.txt").exists()


def test_recursive_delete_removes_the_tree(client):
    c, _, ws, _, _ = client
    r = c.delete("/fs/entry", params={"path": "sub", "recursive": True})
    assert r.status_code == 200, r.text
    assert not (ws / "sub").exists()


def test_a_directory_that_LOOKS_empty_still_reports_what_it_holds(client):
    """The likeliest way to lose work here.

    `_build_tree` hides dotfiles and skips node_modules/.git/dist/... So a
    directory can render as empty in the file tree while holding a great deal,
    and "delete this empty folder" is an easy click to make. The count in the
    refusal must therefore be the TRUE count — what will actually be destroyed —
    not what the tree chose to show.
    """
    c, _, ws, _, _ = client
    d = ws / "looks-empty"
    (d / "node_modules" / "pkg").mkdir(parents=True)
    (d / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    (d / ".env").write_text("SECRET=1", encoding="utf-8")

    # The tree shows it as having no children at all...
    tree = c.get("/fs/tree").json()["tree"]
    node = next(n for n in tree["children"] if n["name"] == "looks-empty")
    assert node["children"] == [], "this test is built on the tree hiding these"

    # ...but the refusal tells the truth.
    r = c.delete("/fs/entry", params={"path": "looks-empty"})
    assert r.status_code == 409
    assert r.json()["detail"]["entries"] >= 4, r.json()

    # And so does the route the UI asks before prompting.
    info = c.get("/fs/entry-info", params={"path": "looks-empty"}).json()
    assert info["entries"] >= 4
    assert info["hidden_from_tree"] is True


def test_a_huge_directory_is_refused_even_with_recursive(client):
    """A blast-radius limit, not a security control. Someone who meant to delete
    a scratch folder and hit the wrong row should be stopped by the size of what
    they are about to lose."""
    c, fs, ws, _, _ = client
    big = ws / "big"
    big.mkdir()
    for i in range(fs.MAX_RECURSIVE_DELETE + 5):
        (big / f"f{i}.txt").write_text("x", encoding="utf-8")
    r = c.delete("/fs/entry", params={"path": "big", "recursive": True})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "too_many_entries"
    assert big.exists()


def test_a_symlink_pointing_out_of_the_workspace_cannot_be_deleted_through(client):
    """A link out is refused, and the thing it points at is untouched.

    This asserts what the code ACTUALLY does, which is worth stating plainly:
    `_safe_resolve` returns the RESOLVED path, so delete operates on a link's
    TARGET rather than on the link. For read and write that is exactly right —
    you want containment checked against the bytes you will touch. For unlink it
    is the POSIX-surprising choice, and it has one consequence worth knowing:
    a link inside the workspace pointing at another file inside the workspace
    would be "deleted" by removing that other file.

    Left as-is deliberately. Fixing it means resolving the parent and keeping the
    leaf unresolved — a SECOND authorisation path through this module, and two
    gates instead of one is how `_ROOTS` went wrong in the first place. Nothing
    in the API creates a symlink (only /fs/write writes, and it writes files), so
    the case cannot arise today. If symlinks ever become creatable here, fix the
    behaviour and rewrite this test rather than discovering it in production.
    """
    c, _, ws, _, secret = client
    link = ws / "escape"
    try:
        link.symlink_to(secret, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this host")

    r = c.delete("/fs/entry", params={"path": "escape/.env", "recursive": True})
    assert r.status_code == 403, r.text
    assert (secret / ".env").exists(), "a file outside the workspace was deleted through a link"


# ------------------------------------------------------------- rename ------

@pytest.mark.parametrize("attack", ESCAPES)
def test_rename_source_cannot_climb_out(client, attack):
    c, _, _, _, secret = client
    r = c.post("/fs/rename", json={"path": attack, "to": "stolen.env"})
    assert r.status_code == 403
    assert (secret / ".env").exists()


@pytest.mark.parametrize("attack", ESCAPES + ["../outside/planted.txt"])
def test_rename_DESTINATION_cannot_climb_out(client, attack):
    """The half that would be easy to forget. `to` is caller-supplied too, and
    an unvalidated destination is an arbitrary write primitive: it moves a file
    the caller owns to any path the process can reach."""
    c, _, ws, _, secret = client
    r = c.post("/fs/rename", json={"path": "keep.py", "to": attack})
    assert r.status_code == 403, f"destination {attack} was accepted"
    assert (ws / "keep.py").exists(), "the source was moved anyway"
    assert not (secret / "planted.txt").exists()


def test_rename_cannot_move_a_file_into_another_account(client):
    c, _, ws, other, _ = client
    r = c.post("/fs/rename", json={"path": "keep.py", "to": f"../{OTHER}/planted.py"})
    assert r.status_code == 403
    assert (ws / "keep.py").exists()
    assert not (other / "planted.py").exists()


def test_renaming_works(client):
    c, _, ws, _, _ = client
    r = c.post("/fs/rename", json={"path": "keep.py", "to": "moved/renamed.py"})
    assert r.status_code == 200, r.text
    body = r.json()
    # The client needs BOTH so it can retarget any open tab.
    assert body["from"] == "keep.py" and body["to"] == "moved/renamed.py"
    assert not (ws / "keep.py").exists()
    assert (ws / "moved" / "renamed.py").read_text(encoding="utf-8") == "print('keep')\n"


def test_rename_refuses_to_overwrite(client):
    """Silently replacing the destination is how a rename eats a file."""
    c, _, ws, _, _ = client
    (ws / "target.py").write_text("DO NOT LOSE ME\n", encoding="utf-8")
    r = c.post("/fs/rename", json={"path": "keep.py", "to": "target.py"})
    assert r.status_code == 409
    assert (ws / "target.py").read_text(encoding="utf-8") == "DO NOT LOSE ME\n"
    assert (ws / "keep.py").exists()


def test_a_folder_cannot_be_moved_into_itself(client):
    """Both paths are inside the workspace, so every containment check passes.
    What happens next is an unreachable subtree or a confusing errno."""
    c, _, ws, _, _ = client
    r = c.post("/fs/rename", json={"path": "sub", "to": "sub/deeper"})
    assert r.status_code == 400
    assert (ws / "sub" / "inner.txt").exists()


def test_the_workspace_itself_cannot_be_renamed(client):
    c, _, ws, _, _ = client
    assert c.post("/fs/rename", json={"path": "", "to": "elsewhere"}).status_code == 400
    assert (ws / "keep.py").exists()


def test_renaming_something_missing_is_404(client):
    c, _, _, _, _ = client
    assert c.post("/fs/rename", json={"path": "ghost.py", "to": "x.py"}).status_code == 404


# ------------------------------------------------------ lost updates -------

def test_a_stale_version_is_refused(client):
    """Two Code tiles on one file is enough to hit this — `code` is a
    multi-instance kind, so it needs no second device. Without the check the
    first save is silently destroyed and nobody is told."""
    c, _, ws, _, _ = client
    opened = c.get("/fs/read", params={"path": "keep.py"}).json()
    v = opened["version"]
    assert v.startswith("sha256:")

    # Somebody else saves first.
    assert c.post("/fs/write", json={"path": "keep.py", "content": "theirs\n"}).status_code == 200

    # Our save still carries the version we opened with.
    r = c.post("/fs/write", json={"path": "keep.py", "content": "ours\n", "expect_version": v})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "changed_on_server"
    assert (ws / "keep.py").read_text(encoding="utf-8") == "theirs\n", "ours won anyway"


def test_the_conflict_response_does_not_carry_the_file(client):
    """A 409 should say what happened, not become a second way to read."""
    c, _, _, _, _ = client
    v = c.get("/fs/read", params={"path": "keep.py"}).json()["version"]
    c.post("/fs/write", json={"path": "keep.py", "content": "CANARY-CONTENT\n"})
    r = c.post("/fs/write", json={"path": "keep.py", "content": "x", "expect_version": v})
    assert "CANARY-CONTENT" not in r.text


def test_a_fresh_version_is_accepted_and_advances(client):
    c, _, _, _, _ = client
    v1 = c.get("/fs/read", params={"path": "keep.py"}).json()["version"]
    r = c.post("/fs/write", json={"path": "keep.py", "content": "next\n", "expect_version": v1})
    assert r.status_code == 200, r.text
    v2 = r.json()["version"]
    assert v2 and v2 != v1
    # And the version the write handed back is immediately usable.
    assert c.post("/fs/write",
                  json={"path": "keep.py", "content": "again\n", "expect_version": v2}
                  ).status_code == 200


def test_saving_an_unchanged_file_is_not_a_conflict(client):
    """The false-conflict trap. If read -> write does not round-trip byte for
    byte, every save of an untouched file reads as somebody else's edit. CRLF
    was exactly that bug — see test_fs_confinement."""
    c, _, ws, _, _ = client
    (ws / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    got = c.get("/fs/read", params={"path": "crlf.txt"}).json()
    r = c.post("/fs/write", json={"path": "crlf.txt",
                                  "content": got["content"],
                                  "expect_version": got["version"]})
    assert r.status_code == 200, r.text
    assert (ws / "crlf.txt").read_bytes() == b"a\r\nb\r\n"


def test_creating_a_new_file_uses_the_empty_version(client):
    """"" is the version of a file that does not exist yet, so "create only if
    nothing is there" needs no separate route."""
    c, _, ws, _, _ = client
    r = c.post("/fs/write", json={"path": "brand-new.txt", "content": "hi", "expect_version": ""})
    assert r.status_code == 200, r.text
    assert (ws / "brand-new.txt").exists()

    # A second create with the same expectation now conflicts.
    r2 = c.post("/fs/write", json={"path": "brand-new.txt", "content": "clobber", "expect_version": ""})
    assert r2.status_code == 409
    assert (ws / "brand-new.txt").read_text(encoding="utf-8") == "hi"


def test_omitting_the_version_still_writes(client):
    """Backwards compatible on purpose: a caller that never read the file has no
    version to send, and forcing one would only make it invent a value."""
    c, _, ws, _, _ = client
    assert c.post("/fs/write", json={"path": "keep.py", "content": "forced\n"}).status_code == 200
    assert (ws / "keep.py").read_text(encoding="utf-8") == "forced\n"


# ------------------------------------------------ the helpers' own gate ----

def test_the_helpers_refuse_a_path_they_were_not_given_authority_over(client):
    """`version_of` and `_count_entries` take an already-resolved Path and an
    account_id. The ownership ratchet made me add that parameter; this makes it
    load-bearing rather than decorative.

    Both are only ever called with a path that came from `_safe_resolve`. That
    is a convention, and conventions are what the next caller forgets — so the
    invariant is checked, and this is the proof that the check exists.
    """
    from fastapi import HTTPException
    _, fs, _, other, secret = client

    for outsider in (secret / ".env", other / "theirs.py"):
        with pytest.raises(HTTPException) as ei:
            fs.version_of(outsider, account_id=ACCT)
        assert ei.value.status_code == 403

    with pytest.raises(HTTPException) as ei:
        fs._count_entries(secret, account_id=ACCT)
    assert ei.value.status_code == 403


def test_the_helpers_still_work_on_our_own_files(client):
    """The other half — a guard that refuses everything is not a guard."""
    _, fs, ws, _, _ = client
    v = fs.version_of(ws / "keep.py", account_id=ACCT)
    assert v.startswith("sha256:")
    assert fs._count_entries(ws / "sub", account_id=ACCT) == 1
