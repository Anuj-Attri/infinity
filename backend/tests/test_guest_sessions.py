"""Guest sessions: each browser tab is its own account, and closing it deletes it.

What these protect, in order of how badly it would go wrong:
  1. One guest can never see another guest's state, or the owner's.
  2. Ending a session deletes everything it wrote, in every store.
  3. A guest cannot change the instance for everyone else.
  4. A reload does not destroy the session it is reloading.
"""

from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

OWNER = "00000000-0000-0000-0000-00000000000f"   # accounts.SINGLE_TENANT.id


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("GUEST_SESSIONS", "1")
    import app.paths, app.auth, app.fs, app.vault, app.profile, app.guest
    import app.agent.store, app.main
    importlib.reload(app.paths)
    importlib.reload(app.auth)
    importlib.reload(app.fs)
    importlib.reload(app.vault)
    importlib.reload(app.profile)
    importlib.reload(app.guest)
    importlib.reload(app.agent.store)
    main = importlib.reload(app.main)
    main._register_guest_purgers()
    return main, app.guest, app.fs, app.vault, tmp_path / "data"


def _start(client) -> dict[str, str]:
    r = client.post("/guest/start")
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _fill(client, h, tag: str) -> None:
    """Write something into every store a guest can reach from the UI."""
    assert client.put("/profile", json={"display_name": tag}, headers=h).status_code == 200
    r = client.post("/fs/write", json={"path": f"{tag}.py", "content": f"# {tag}\n",
                                        "expect_version": ""}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/nova/permissions", json={"tool": "web_search", "level": "always"},
                    headers=h)
    assert r.status_code == 200, r.text


def test_guest_mode_is_off_unless_asked_for(monkeypatch, env):
    main, *_ = env
    monkeypatch.delenv("GUEST_SESSIONS")
    c = TestClient(main.app)
    assert c.post("/guest/start").status_code == 404
    assert c.get("/auth/status").json()["guest_mode"] is False
    assert "=false" in c.get("/runtime-config.js").text


def test_two_guests_cannot_see_each_other(env):
    main, guest, fs, vault, data = env
    c = TestClient(main.app)
    a, b = _start(c), _start(c)
    _fill(c, a, "alice")

    assert c.get("/profile", headers=b).json()["display_name"] == ""
    tree = c.get("/fs/tree", headers=b).json()
    assert "alice" not in str(tree)
    assert c.get("/nova/permissions", headers=b).json()["permissions"] == {}
    # And the owner's account is untouched by a guest's writes.
    from app.profile import display_name
    assert display_name(OWNER) == ""


def test_ending_a_session_deletes_everything_it_wrote(env):
    main, guest, fs, vault, data = env
    c = TestClient(main.app)
    h = _start(c)
    _fill(c, h, "carol")
    gid = guest.lookup(h["Authorization"][7:]).id
    from app.profile import display_name
    main.MEMORY.add_memory("carol likes tea", account_id=gid)
    (vault.vault_dir(gid) / "note.md").write_text("# carol", encoding="utf-8")
    assert (fs.WORKSPACE_ROOT / gid / "carol.py").exists()
    assert display_name(gid) == "carol"
    assert main.MEMORY.list_memories(account_id=gid)

    assert c.post("/guest/end", content=h["Authorization"][7:]).json()["ok"] is True
    # Nothing is deleted inside the grace period: a reload sends the same event.
    assert guest.sweep(now=time.time() + 1) == 0
    assert guest.sweep(now=time.time() + guest.GRACE_S + 1) == 1

    assert not (fs.WORKSPACE_ROOT / gid).exists()
    assert not (vault._VAULT_ROOT / gid).exists()
    assert main.MEMORY.list_memories(account_id=gid) == []
    assert display_name(gid) == ""
    assert gid not in main._read_permission_file()
    assert gid not in guest._read_registry()
    # The token is dead: the next request is a plain 401, not someone's data.
    assert c.get("/profile", headers=h).status_code == 401


def test_a_reload_inside_the_grace_period_keeps_the_session(env):
    main, guest, *_ = env
    c = TestClient(main.app)
    h = _start(c)
    _fill(c, h, "dave")
    c.post("/guest/end", content=h["Authorization"][7:])
    # The reloaded page makes a request with the same token.
    assert c.get("/profile", headers=h).json()["display_name"] == "dave"
    assert guest.sweep(now=time.time() + guest.GRACE_S + 1) == 0
    assert c.get("/profile", headers=h).json()["display_name"] == "dave"


def test_an_abandoned_session_is_deleted_when_idle(monkeypatch, env):
    """The close event is best-effort. The idle sweep is the guarantee."""
    main, guest, fs, *_ = env
    monkeypatch.setenv("GUEST_IDLE_MINUTES", "5")
    c = TestClient(main.app)
    h = _start(c)
    _fill(c, h, "erin")
    gid = guest.lookup(h["Authorization"][7:]).id
    assert guest.sweep(now=time.time() + 4 * 60) == 0
    assert guest.sweep(now=time.time() + 6 * 60) == 1
    assert not (fs.WORKSPACE_ROOT / gid).exists()


def test_signing_out_deletes_at_once(env):
    main, guest, fs, *_ = env
    c = TestClient(main.app)
    h = _start(c)
    _fill(c, h, "fay")
    gid = guest.lookup(h["Authorization"][7:]).id
    assert c.post("/auth/logout", headers=h).json()["deleted"] is True
    assert not (fs.WORKSPACE_ROOT / gid).exists()


def test_a_restart_cleans_up_what_guests_left_behind(env):
    main, guest, fs, *_ = env
    c = TestClient(main.app)
    h = _start(c)
    _fill(c, h, "gus")
    gid = guest.lookup(h["Authorization"][7:]).id
    guest._sessions.clear()            # what a process restart does to tokens
    assert guest.purge_orphans() == 1
    assert not (fs.WORKSPACE_ROOT / gid).exists()
    assert guest._read_registry() == []


@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/diagnostics"),
    ("POST", "/admin/killswitch"),
    ("GET", "/auth/devices"),
    ("POST", "/auth/reset"),
    ("POST", "/chat-providers/openrouter/connect"),
    ("POST", "/mcp/connect"),
    ("POST", "/mcp/enabled"),
    ("GET", "/mcp/audit"),
    ("POST", "/skills/toggle"),
    ("POST", "/spend/cap"),
    ("GET", "/spend/summary"),
    ("GET", "/usage/all"),
    ("POST", "/notify/test"),
])
def test_a_guest_cannot_change_the_instance(env, method, path):
    main, *_ = env
    c = TestClient(main.app)
    h = _start(c)
    assert c.request(method, path, headers=h, json={}).status_code == 403


def test_a_guest_gets_no_operator_notifications(env):
    main, *_ = env
    main._notify.enqueue(category="job", title="operator-only", body="x", confidence=1.0)
    c = TestClient(main.app)
    h = _start(c)
    assert c.get("/notify/poll", headers=h).json()["items"] == []


def test_the_owner_account_can_never_be_purged(env):
    _, guest, *_ = env
    with pytest.raises(ValueError):
        guest.purge(OWNER)
    with pytest.raises(ValueError):
        guest.purge("../../etc")


def test_every_account_scoped_store_has_a_purger(env):
    """A store that files data under an account but is missing here would
    outlive the guest who wrote it."""
    _, guest, *_ = env
    assert set(guest.purger_names()) >= {
        "missions", "vault", "workspace", "memory", "profile", "permissions"}


def test_the_instance_refuses_new_guests_when_full(monkeypatch, env):
    main, *_ = env
    monkeypatch.setenv("GUEST_MAX_SESSIONS", "2")
    c = TestClient(main.app)
    _start(c)
    _start(c)
    assert c.post("/guest/start").status_code == 503


def test_guest_mode_opens_the_door_to_the_public(env, remote_client):
    main, *_ = env
    c = remote_client(main.app)
    assert c.get("/auth/status").status_code == 200
    assert c.post("/guest/start").status_code == 200


def test_the_storage_shim_is_served_in_guest_mode(env):
    main, *_ = env
    js = TestClient(main.app).get("/runtime-config.js").text
    assert "__INFINITY_GUEST__=true" in js
    assert "sessionStorage" in js


def test_allow_public_opens_the_door_without_guest_mode(monkeypatch, env, remote_client):
    """ALLOW_PUBLIC is for an instance with nothing private in it, such as a
    local Docker run where the browser is not loopback from inside the box."""
    main, *_ = env
    monkeypatch.delenv("GUEST_SESSIONS")
    assert remote_client(main.app).get("/auth/status").status_code == 404
    monkeypatch.setenv("ALLOW_PUBLIC", "1")
    assert remote_client(main.app).get("/auth/status").status_code == 200
