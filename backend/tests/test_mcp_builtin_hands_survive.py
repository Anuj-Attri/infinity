"""Nova's own hands are not a connection the user made.

`mcp.hands.json` seeds a built-in server (`hand-app`) that is how Nova switches
models, opens panels, and runs /image, /audio, /research and /summarize. It is
seeded by `ensure_builtin_servers()` — at STARTUP, and only at startup.

`disconnect_all()` was `_save({})`. It emptied the whole registry, built-ins
included, so one click on "Disconnect all" in Settings silently removed Nova's
hands until the next redeploy. The single-server Remove had the same effect on
its own, and the UI offered that button on the built-in row because the type it
rendered from omitted the `builtin` field the backend was already sending.

Nothing in the app said any of this had happened. The tools simply stopped
existing, and the model — which is only told about tools that are exposed —
would answer as though it had never had them. That is the worst shape a bug can
have: a capability disappears and the system that lost it reports no error.

The rule is narrow and easy to state: a user can remove anything they added,
and cannot remove anything they did not.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def mcp(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.paths, app.mcp_client
    importlib.reload(app.paths)
    m = importlib.reload(app.mcp_client)
    m.ensure_builtin_servers()
    return m


def _ids(m, *, builtin: bool | None = None) -> set[str]:
    out = set()
    for s in m.list_servers():
        if builtin is None or bool(s.get("builtin")) is builtin:
            out.add(s["id"])
    return out


def test_the_hands_are_seeded_at_all(mcp):
    """If this fails the rest of the file proves nothing — every other test
    would pass vacuously against an empty registry."""
    assert _ids(mcp, builtin=True), (
        "no built-in server was seeded, so the protections below are untested"
    )


def test_disconnect_all_keeps_the_builtin_hands(mcp):
    before = _ids(mcp, builtin=True)
    mcp.disconnect_all()
    assert _ids(mcp, builtin=True) == before, (
        "'Disconnect all' removed Nova's own hands; they are only re-seeded at "
        "startup, so the app loses model switching, panels and /commands until "
        "the next redeploy"
    )


def test_disconnect_all_still_removes_what_the_user_connected(mcp):
    """The mirror image. A kill switch that keeps everything is not a kill
    switch, and this is the assertion that would fail if someone "fixed" the
    test above by making disconnect_all a no-op."""
    d = mcp._load()
    d["srv-user"] = {"name": "Theirs", "url": "https://example.com/mcp",
                     "transport": "http", "trusted": True}
    mcp._save(d)
    assert "srv-user" in _ids(mcp)

    removed = mcp.disconnect_all()
    assert "srv-user" not in _ids(mcp)
    assert removed == 1, f"reported {removed} removed; the count must exclude built-ins"


def test_a_builtin_cannot_be_removed_one_at_a_time(mcp):
    """Same hole, reachable through the per-server Remove button."""
    sid = sorted(_ids(mcp, builtin=True))[0]
    assert mcp.disconnect(sid) is False, "the backend agreed to remove a built-in"
    assert sid in _ids(mcp, builtin=True)


def test_a_user_server_can_still_be_removed(mcp):
    d = mcp._load()
    d["srv-user"] = {"name": "Theirs", "url": "https://example.com/mcp",
                     "transport": "http", "trusted": True}
    mcp._save(d)
    assert mcp.disconnect("srv-user") is True
    assert "srv-user" not in _ids(mcp)


def test_the_listing_says_which_servers_are_builtin(mcp):
    """The UI cannot hide a Remove button it has no way to identify. The
    backend sent this field all along; the frontend type dropped it."""
    for s in mcp.list_servers():
        assert "builtin" in s, f"{s['id']} does not report whether it is built in"
    assert any(s["builtin"] for s in mcp.list_servers())


def test_every_marketplace_suggestion_has_a_real_url():
    """A one-click suggestion that prefills an unresolvable placeholder fails
    with a network error, and the user cannot tell whether MCP is broken, the
    server is down, or the entry was never real. Three of five were
    `https://<your-search-mcp>/mcp` and friends."""
    from app.mcp_api import MARKETPLACE
    bad = [m["name"] for m in MARKETPLACE
           if "<" in m["url_hint"] and m["category"] != "custom"]
    assert not bad, (
        "marketplace entries with placeholder URLs that cannot connect: "
        + ", ".join(bad)
    )
