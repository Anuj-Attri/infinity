"""First-run setup is the one endpoint that cannot authenticate its caller.

There is no credential yet, so /auth/setup is unauthenticated by nature. On a
laptop reachable only from loopback that is fine. On a public URL it is an
account-takeover endpoint: whoever POSTs first sets the master password, mints
themselves a device token, and locks the owner out of their own deployment. It
only 409s once a password EXISTS, which is too late.

SETUP_TOKEN is the out-of-band proof that the caller controls the deployment.
These tests pin all four branches, because getting this subtly wrong looks
exactly like getting it right.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with(monkeypatch, tmp_path):
    """Build the app with a given environment and a throwaway data directory.

    The data directory matters: without it these tests would set a master
    password in the developer's real .data and every later run would 409.
    """
    def _build(**env: str):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        for k in ("SETUP_TOKEN", "MARK4_DEV_ACCOUNT"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        # Re-import so module-level env reads (_SETUP_TOKEN, data paths) re-run.
        import app.paths, app.auth, app.main
        importlib.reload(app.paths)
        importlib.reload(app.auth)
        main = importlib.reload(app.main)
        return TestClient(main.app), main
    return _build


# TestClient reports client.host as "testclient" — NOT loopback — which is the
# same branch a proxied request from the internet lands in. That is what makes
# these tests meaningful rather than a loopback special case.

def test_public_caller_cannot_claim_without_a_token(app_with):
    client, _ = app_with()
    r = client.post("/auth/setup", json={"password": "stranger-owns-it"})
    assert r.status_code == 403, "a stranger claimed the instance"
    assert "setup" in r.json()["detail"].lower()


def test_status_advertises_that_setup_is_unavailable(app_with):
    """The UI needs to know, or it shows a form that can only ever fail."""
    client, _ = app_with()
    body = client.get("/auth/status").json()
    assert body["setup_available"] is False
    assert body["setup_token_required"] is False


def test_wrong_token_is_refused(app_with):
    client, _ = app_with(SETUP_TOKEN="correct-horse")
    r = client.post("/auth/setup", json={"password": "abcdef", "setup_token": "wrong"})
    assert r.status_code == 403


def test_missing_token_is_refused_when_one_is_required(app_with):
    client, _ = app_with(SETUP_TOKEN="correct-horse")
    r = client.post("/auth/setup", json={"password": "abcdef"})
    assert r.status_code == 403


def test_correct_token_claims_the_instance_exactly_once(app_with):
    client, _ = app_with(SETUP_TOKEN="correct-horse")
    body = client.get("/auth/status").json()
    assert body["setup_token_required"] is True
    assert body["setup_available"] is True

    r = client.post("/auth/setup", json={"password": "abcdef", "setup_token": "correct-horse"})
    assert r.status_code == 200, r.text

    # Replay must not re-claim, even with the right token.
    again = client.post("/auth/setup", json={"password": "hijack", "setup_token": "correct-horse"})
    assert again.status_code == 409


def test_short_password_is_still_rejected_behind_a_valid_token(app_with):
    client, _ = app_with(SETUP_TOKEN="correct-horse")
    r = client.post("/auth/setup", json={"password": "abc", "setup_token": "correct-horse"})
    assert r.status_code == 422


def test_protected_routes_refuse_without_a_session(app_with):
    """The gate is app-level: anything not explicitly exempt must 401."""
    client, _ = app_with()
    for path in ("/vault/stats", "/mcp/servers", "/memory", "/skills",
                 "/spend/summary", "/admin/diagnostics"):
        assert client.get(path).status_code == 401, f"{path} was reachable unauthenticated"


def test_bootstrap_routes_stay_reachable(app_with):
    """/health and /echo/stream are called by CI with no credentials."""
    client, _ = app_with()
    assert client.get("/health").status_code == 200
    assert client.get("/auth/status").status_code == 200
    assert client.post("/echo/stream", json={"text": "a b", "delay_ms": 0}).status_code == 200
