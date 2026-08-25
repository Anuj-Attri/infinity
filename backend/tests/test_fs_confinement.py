"""`/fs` may only ever touch the workspace.

These endpoints were lifted verbatim from the desktop build, where serving the
developer's own disk to the developer is a reasonable thing for a local tool to
do. Hosted, the same code was an arbitrary read/write primitive on the container,
available to anyone who got through the shared password:

    GET  /fs/tree?path=/srv          -> registers /srv as a "project root"
    POST /fs/write  /srv/dist/index.html
                                     -> persistent XSS served to every visitor
                                        from the app's own origin

`/fs/read` made it worse by auto-registering a file's parent, so READING a file
widened what could later be WRITTEN. The root set was process-global, so it was
shared by every user rather than scoped to any of them.

The replacement holds one line: resolve the path, then require the result to be
inside WORKSPACE_ROOT. The tests below are mostly that attack chain, written down
so it cannot come back — the module is scheduled to be reworked again at M6 for
per-account storage, and this is what must survive that.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

ACCT = "00000000-0000-0000-0000-00000000000f"   # SINGLE_TENANT


@pytest.fixture
def client(monkeypatch, tmp_path):
    """App with a throwaway workspace, plus a sensitive file OUTSIDE it."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)

    import app.paths, app.fs, app.auth, app.main
    importlib.reload(app.paths)
    fs = importlib.reload(app.fs)
    importlib.reload(app.auth)
    main = importlib.reload(app.main)
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {
        "/fs/tree", "/fs/read", "/fs/write",
    })

    ws = fs.workspace_dir(ACCT)
    (ws / "hello.py").write_text("print('hi')\n", encoding="utf-8")

    # The things an escape would be after. Deliberately outside the workspace.
    secret = tmp_path / "outside"
    secret.mkdir(parents=True, exist_ok=True)
    (secret / ".env").write_text("OPENROUTER_API_KEY=sk-or-CANARY\n", encoding="utf-8")
    (secret / "index.html").write_text("<div id='root'></div>", encoding="utf-8")

    return TestClient(main.app), fs, ws, secret


# ---------------------------------------------------------------- reading ---

def test_the_workspace_itself_still_works(client):
    """The confinement must not break the feature it protects."""
    c, _, ws, _ = client
    r = c.get("/fs/tree")
    assert r.status_code == 200, r.text
    # The root is the RELATIVE root now, i.e. "". It used to be the absolute
    # server path, which named the account and published the server's layout.
    assert r.json()["root"] == ""

    # An absolute path is still ACCEPTED — _safe_resolve confines it either way,
    # and refusing it outright would be a behaviour change for no security gain.
    # What changed is that the server no longer HANDS one out.
    r = c.get("/fs/read", params={"path": str(ws / "hello.py")})
    assert r.status_code == 200
    assert "print" in r.json()["content"]


def test_nothing_the_client_receives_names_the_account_or_the_server(client):
    """The owner's screenshot showed the account UUID as the file tree's root
    folder, because the workspace directory IS named after the account and the
    tree sent `str(path)` for every node.

    Asserted over the WHOLE response rather than a field at a time: the leak was
    in `path` on every node AND in the top-level `root`, and a per-field check
    would have been written against whichever one someone happened to notice.
    """
    import json
    c, _, ws, _ = client
    from app.accounts import SINGLE_TENANT

    (ws / "sub").mkdir(exist_ok=True)
    (ws / "sub" / "deep.txt").write_text("x", encoding="utf-8")

    blob = json.dumps(c.get("/fs/tree").json())
    assert SINGLE_TENANT.id not in blob, "the account id is in the tree response"
    assert str(ws) not in blob, "an absolute server path is in the tree response"
    assert ws.name not in blob, "the workspace directory's real name leaked"

    # And the same for the two routes that echo a path back.
    blob = json.dumps(c.get("/fs/read", params={"path": "sub/deep.txt"}).json())
    assert SINGLE_TENANT.id not in blob and str(ws) not in blob

    blob = json.dumps(c.post("/fs/write", json={"path": "sub/deep.txt", "content": "y"}).json())
    assert SINGLE_TENANT.id not in blob and str(ws) not in blob


def test_the_paths_it_hands_out_are_the_paths_it_accepts(client):
    """A round trip. Relative paths would be worthless if the client could not
    send back what it was given — and this is exactly the kind of thing that
    breaks on Windows, where `str(Path)` uses backslashes and the wire format
    is POSIX."""
    c, _, ws, _ = client
    (ws / "sub").mkdir(exist_ok=True)
    (ws / "sub" / "round.txt").write_text("trip", encoding="utf-8")

    tree = c.get("/fs/tree").json()["tree"]
    found = []

    def walk(n):
        if n["kind"] == "file":
            found.append(n["path"])
        else:
            for ch in n.get("children", []):
                walk(ch)

    walk(tree)
    assert "sub/round.txt" in found, f"expected a posix relative path, got {found}"
    assert not any("\\" in p for p in found), f"a backslash reached the wire: {found}"

    for p in found:
        assert c.get("/fs/read", params={"path": p}).status_code == 200, p


def test_a_relative_path_is_relative_to_the_workspace(client):
    """Not to the process CWD, which in the container is the app root."""
    c, _, _, _ = client
    r = c.get("/fs/read", params={"path": "hello.py"})
    assert r.status_code == 200
    assert "print" in r.json()["content"]


@pytest.mark.parametrize("attack", [
    "../outside/.env",
    "../../outside/.env",
    "./../outside/.env",
    "subdir/../../outside/.env",
])
def test_dot_dot_cannot_climb_out(client, attack):
    c, _, _, _ = client
    r = c.get("/fs/read", params={"path": attack})
    assert r.status_code == 403, f"{attack} escaped the workspace: {r.status_code}"


def test_an_absolute_path_outside_is_refused(client):
    c, _, _, secret = client
    r = c.get("/fs/read", params={"path": str(secret / ".env")})
    assert r.status_code == 403
    assert "CANARY" not in r.text


def test_refusal_does_not_reveal_whether_the_path_exists(client):
    """A distinguishable 404 would make this a filesystem-layout probe."""
    c, _, _, secret = client
    real = c.get("/fs/read", params={"path": str(secret / ".env")})
    fake = c.get("/fs/read", params={"path": str(secret / "does-not-exist-at-all")})
    assert real.status_code == fake.status_code == 403
    assert real.json()["detail"] == fake.json()["detail"]


def test_a_symlink_out_of_the_workspace_does_not_escape(client, tmp_path):
    """resolve() follows the link BEFORE the containment test."""
    c, _, ws, secret = client
    link = ws / "escape"
    try:
        link.symlink_to(secret, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this host")
    r = c.get("/fs/read", params={"path": str(link / ".env")})
    assert r.status_code == 403
    assert "CANARY" not in r.text


# ---------------------------------------------------------------- writing ---

def test_writing_inside_the_workspace_works(client):
    c, _, ws, _ = client
    r = c.post("/fs/write", json={"path": "notes/new.txt", "content": "hello"})
    assert r.status_code == 200, r.text
    assert (ws / "notes" / "new.txt").read_text(encoding="utf-8") == "hello"


def test_writing_outside_the_workspace_is_refused(client):
    c, _, _, secret = client
    target = secret / "index.html"
    before = target.read_text(encoding="utf-8")
    r = c.post("/fs/write", json={"path": str(target), "content": "<script>pwn()</script>"})
    assert r.status_code == 403
    assert target.read_text(encoding="utf-8") == before, "the file outside was overwritten"


def test_the_original_attack_chain_is_dead(client):
    """The exact sequence that was possible: name a root, then write into it.

    Naming a directory used to BE the grant. This asserts that listing something
    outside the workspace neither succeeds nor unlocks a later write.
    """
    c, _, _, secret = client

    listed = c.get("/fs/tree", params={"path": str(secret)})
    assert listed.status_code == 403, "listing an arbitrary directory must be refused"

    target = secret / "index.html"
    before = target.read_text(encoding="utf-8")
    wrote = c.post("/fs/write", json={"path": str(target), "content": "<script>pwn()</script>"})
    assert wrote.status_code == 403
    assert target.read_text(encoding="utf-8") == before


def test_reading_a_file_does_not_widen_what_can_be_written(client):
    """/fs/read used to auto-register the parent directory as a root."""
    c, _, ws, _ = client
    # A legitimate read inside the workspace.
    assert c.get("/fs/read", params={"path": "hello.py"}).status_code == 200
    # Must not have granted anything about the workspace's PARENT.
    outside = ws.parent / "sibling.txt"
    r = c.post("/fs/write", json={"path": str(outside), "content": "x"})
    assert r.status_code == 403
    assert not outside.exists()


# ---------------------------------------------------------------- hygiene ---

def test_env_files_are_not_listed_in_the_tree(client):
    """The desktop build un-hid .env on purpose. Hosted, it is the prize."""
    c, _, ws, _ = client
    (ws / ".env").write_text("OPENROUTER_API_KEY=sk-or-INSIDE-CANARY\n", encoding="utf-8")
    body = c.get("/fs/tree").text
    assert ".env" not in body, "the tree advertises .env"
    assert "CANARY" not in body


def test_no_process_global_root_set_remains(client):
    """The shared mutable state that made this a cross-user problem."""
    _, fs, _, _ = client
    assert not hasattr(fs, "_ROOTS"), (
        "_ROOTS is back. A process-global root set is shared by every user and "
        "is exactly what let one request widen another's reach."
    )


def test_the_workspace_lives_on_the_persistent_volume(client, tmp_path):
    """A workspace on the container's own disk is erased by every redeploy."""
    _, fs, ws, _ = client
    assert str(ws).startswith(str((tmp_path / "data").resolve())), (
        f"workspace {ws} is not under DATA_DIR; it will not survive a redeploy"
    )


# ---------------------------------------------------------------- ownership ---

OTHER = "11111111-1111-1111-1111-111111111111"


def test_each_account_gets_its_own_workspace(client):
    """Code files are FILES. A shared workspace means two accounts editing
    main.py edit the SAME file — every path check here passes and the content is
    still shared. That is the failure a containment check cannot catch, because
    nothing about it is out of bounds."""
    _, fs, ws, _ = client
    theirs = fs.workspace_dir(OTHER)
    assert theirs != ws
    assert theirs.parent == ws.parent


def test_the_same_filename_is_two_different_files(client):
    _, fs, ws, _ = client
    (ws / "main.py").write_text("mine\n", encoding="utf-8")
    (fs.workspace_dir(OTHER) / "main.py").write_text("theirs\n", encoding="utf-8")
    assert (ws / "main.py").read_text(encoding="utf-8") == "mine\n"


def test_one_account_cannot_reach_into_another_s_workspace(client):
    """The containment test does double duty: another account's directory is
    outside YOUR root, so it is refused by exactly the same line that refuses
    /etc/passwd."""
    c, fs, _, _ = client
    theirs = fs.workspace_dir(OTHER)
    (theirs / "secret.py").write_text("their private code\n", encoding="utf-8")

    r = c.get("/fs/read", params={"path": str(theirs / "secret.py")})
    assert r.status_code == 403, "read another account's file"
    assert "their private code" not in r.text

    before = (theirs / "secret.py").read_text(encoding="utf-8")
    w = c.post("/fs/write", json={"path": str(theirs / "secret.py"), "content": "tampered"})
    assert w.status_code == 403, "wrote into another account's workspace"
    assert (theirs / "secret.py").read_text(encoding="utf-8") == before

    t = c.get("/fs/tree", params={"path": str(theirs)})
    assert t.status_code == 403, "listed another account's workspace"


def test_pre_ownership_files_move_into_the_single_tenant_workspace(monkeypatch, tmp_path):
    """Flat layout -> per-account, once, on first use. Honest only while there
    IS one tenant; later it would be a guess about whose files these were."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    import app.paths
    importlib.reload(app.paths)

    flat = app.paths.data_path("workspace")
    flat.mkdir(parents=True, exist_ok=True)
    (flat / "legacy.py").write_text("# from before ownership\n", encoding="utf-8")

    import app.fs
    fs = importlib.reload(app.fs)
    d = fs.workspace_dir(ACCT)
    assert (d / "legacy.py").exists(), "a pre-ownership file was left behind"
    assert not (flat / "legacy.py").exists(), "it was copied rather than moved"


# ---------------------------------------------------- round trip -----------

def test_a_file_survives_being_opened_and_saved_unchanged(client):
    """A no-op save must not alter a single byte.

    `read_text` defaults to universal newlines, so every CRLF on disk arrived at
    the editor as LF, and `/write` writes back with newline="" — no translation.
    Opening a Windows-authored file and pressing Ctrl+S without touching a
    character therefore rewrote every line ending in it, silently.

    Autosave would have done that automatically, to every such file, the moment
    it was opened. It also makes any content-hash version scheme unusable: a
    file that cannot round-trip hashes differently on every save, so every save
    reads as a conflict.
    """
    c, _, ws, _ = client
    original = b"first line\r\nsecond line\r\nno trailing newline"
    (ws / "windows.txt").write_bytes(original)

    got = c.get("/fs/read", params={"path": "windows.txt"})
    assert got.status_code == 200
    content = got.json()["content"]
    assert "\r\n" in content, "the CRLF was eaten on the way out"

    assert c.post("/fs/write", json={"path": "windows.txt", "content": content}).status_code == 200
    assert (ws / "windows.txt").read_bytes() == original, "a no-op save changed the file"


def test_an_lf_file_stays_lf(client):
    """The mirror image — the fix must not start INSERTING carriage returns."""
    c, _, ws, _ = client
    original = b"alpha\nbeta\n"
    (ws / "unix.txt").write_bytes(original)
    content = c.get("/fs/read", params={"path": "unix.txt"}).json()["content"]
    assert "\r" not in content
    c.post("/fs/write", json={"path": "unix.txt", "content": content})
    assert (ws / "unix.txt").read_bytes() == original
