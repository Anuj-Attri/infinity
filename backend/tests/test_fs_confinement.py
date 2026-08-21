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

    ws = fs.workspace_root()
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
    assert r.json()["root"] == str(ws)

    r = c.get("/fs/read", params={"path": str(ws / "hello.py")})
    assert r.status_code == 200
    assert "print" in r.json()["content"]


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
