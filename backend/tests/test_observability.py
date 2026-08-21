"""Scrubbing is written and tested BEFORE Sentry can be switched on.

The first event is the one you cannot un-send. A product whose pitch is "nobody
sees what they aren't allowed to see" cannot ship an error reporter that mails
message bodies and document text to a third party.

The second test is the one that actually matters. A scrubbing test that only
asserts "the secret is gone" passes trivially for a config that sends nothing
useful — and then the first real incident is unactionable, somebody loosens the
config to see more, and the scrubbing goes with it.
"""

from __future__ import annotations

import json

from app.observability import before_send, scrub

SECRETS = [
    "sk-or-v1-abcdefghijklmnopqrstuvwxyz012345",
    "sbp_abcdefghijklmnopqrstuvwxyz",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.Xy_signature-part",
    "postgres://admin:hunter2@db.internal:5432/prod",
    "user@example.com",
]


def _event() -> dict:
    return {
        "server_name": "container-7f3a",
        "message": f"failed calling provider with {SECRETS[0]}",
        "user": {"id": "acct-123", "email": SECRETS[4], "ip_address": "203.0.113.9"},
        "request": {
            "url": "https://api.example/chat",
            "method": "POST",
            "query_string": "token=supersecret&q=salary+band",
            "data": {"message": "what is our comp band for senior engineers?"},
            "cookies": {"session": "abc"},
            "headers": {
                "Authorization": f"Bearer {SECRETS[1]}",
                "X-Request-Id": "req-42",
                "User-Agent": "Mozilla/5.0",
            },
        },
        "extra": {
            "database_url": SECRETS[3],
            "chunks": ["The comp band for senior engineers is 180000 to 220000 USD."],
            "retry": 2,
        },
        "exception": {"values": [{
            "type": "ValueError",
            "value": f"bad key {SECRETS[0]}",
            "module": "app.providers.chat",
            "stacktrace": {"frames": [{"lineno": 412, "function": "dispatch"}]},
        }]},
    }


def test_no_secret_or_user_content_survives():
    blob = json.dumps(before_send(_event()))
    for s in SECRETS:
        assert s not in blob, f"leaked: {s[:24]}"
    # User content is as disqualifying as a credential.
    assert "comp band" not in blob, "a message body leaked"
    assert "180000" not in blob, "document content leaked"
    assert "supersecret" not in blob, "a query string leaked"
    # Gotcha 2: server_name is not suppressed by setting it to None.
    assert "container-7f3a" not in blob


def test_the_event_is_still_worth_receiving():
    """Scrubbing that removes everything is a config nobody will keep."""
    out = before_send(_event())
    exc = out["exception"]["values"][0]
    assert exc["type"] == "ValueError"
    assert exc["module"] == "app.providers.chat"
    assert exc["stacktrace"]["frames"][0]["lineno"] == 412
    assert out["request"]["url"] == "https://api.example/chat"
    assert out["request"]["method"] == "POST"
    assert out["request"]["headers"]["X-Request-Id"] == "req-42"
    assert out["extra"]["retry"] == 2
    # The account is identifiable; the person is not.
    assert out["user"] == {"id": "acct-123"}


def test_scrub_is_depth_bounded():
    """The error path must never be the thing that hangs."""
    deep: dict = {}
    node = deep
    for _ in range(200):
        node["next"] = {}
        node = node["next"]
    node["password"] = "x"
    scrub(deep)   # must return rather than recurse forever


def test_sentry_stays_off_without_a_dsn(monkeypatch):
    from app import observability
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observability.init() is False
