"""The operator console reports on secrets. It must never report them.

A diagnostics screen that prints key values leaks them the first time someone
screenshots it for support, pastes it into a chat, or files it with a bug. The
contract is: presence only, never the value. This test holds that line by
planting recognisable fake secrets and asserting none of them appear anywhere in
the response.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

# Deliberately distinctive so a substring search cannot miss them.
FAKE = {
    "OPENROUTER_API_KEY": "sk-or-v1-CANARY-openrouter-must-not-appear",
    "SUPABASE_SERVICE_ROLE_KEY": "eyJCANARYservicerolemustnotappear",
    "SETUP_TOKEN": "CANARY-setup-token-must-not-appear",
    "NEWS_API_KEY": "CANARY-news-key-must-not-appear",
    "VOYAGE_API_KEY": "CANARY-voyage-key-must-not-appear",
    "DATABASE_URL": "postgres://user:CANARYpassword@host/db",
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MARK4_DEV_ACCOUNT", raising=False)
    for k, v in FAKE.items():
        monkeypatch.setenv(k, v)
    import app.paths, app.auth, app.admin, app.main
    importlib.reload(app.paths)
    importlib.reload(app.auth)
    importlib.reload(app.admin)
    main = importlib.reload(app.main)
    # Exempt only the route under test; everything else keeps its gate.
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {"/admin/diagnostics"})
    return TestClient(main.app)


def test_no_secret_value_appears_anywhere_in_the_response(client):
    r = client.get("/admin/diagnostics")
    assert r.status_code == 200
    blob = json.dumps(r.json())
    for name, value in FAKE.items():
        assert value not in blob, f"{name}'s VALUE leaked into diagnostics"
        # The canary marker alone would catch a partial/truncated leak too.
        assert "CANARY" not in blob, f"a fragment of {name} leaked"


def test_presence_is_reported_accurately(client):
    """Presence must be real, not assumed — a green light that means 'we did not
    look' is worse than no light."""
    cfg = {c["name"]: c["set"] for c in client.get("/admin/diagnostics").json()["config"]}
    for name in FAKE:
        assert cfg.get(name) is True, f"{name} is set but reported unset"
    # Something deliberately NOT set must report False.
    assert cfg.get("SENTRY_DSN") is False


def test_every_non_ok_subsystem_names_a_way_out(client):
    """A status with no route out is a complaint, not a diagnostic."""
    for s in client.get("/admin/diagnostics").json()["subsystems"]:
        if s["status"] != "ok":
            assert s["unblock"], f"{s['id']} is {s['status']} with no unblock step"
        assert s["detail"], f"{s['id']} has no detail"


def test_required_config_is_flagged_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import app.paths, app.auth, app.admin, app.main
    importlib.reload(app.paths); importlib.reload(app.auth); importlib.reload(app.admin)
    main = importlib.reload(app.main)
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {"/admin/diagnostics"})
    body = TestClient(main.app).get("/admin/diagnostics").json()

    models = next(s for s in body["subsystems"] if s["id"] == "models")
    assert models["status"] == "blocked"
    assert "OPENROUTER_API_KEY" in (models["unblock"] or "")


def test_ephemeral_storage_is_reported_as_such(monkeypatch, tmp_path):
    """The wipe-on-redeploy behaviour should be expected, not mysterious."""
    monkeypatch.delenv("DATA_DIR", raising=False)
    import app.paths, app.auth, app.admin, app.main
    importlib.reload(app.paths); importlib.reload(app.auth); importlib.reload(app.admin)
    main = importlib.reload(app.main)
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {"/admin/diagnostics"})
    body = TestClient(main.app).get("/admin/diagnostics").json()

    p = next(s for s in body["subsystems"] if s["id"] == "persistence")
    assert p["status"] == "degraded"
    assert "redeploy" in p["detail"].lower()
