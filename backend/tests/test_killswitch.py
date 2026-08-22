"""The killswitch — one control that stops anything which costs money.

The owner's requirement, and the reason it moved earlier than MARK4.md had it:

> "any versions released right now should be protected with killswitch cause i
>  want to save this app."

The definition of done is narrow and worth stating exactly: **the NEXT request
refuses.** Not the next minute, not after a restart, not once a scheduler
notices. A control that takes time to apply is not a killswitch, it is a
preference — and the moment it matters is a runaway bill, which is precisely
when a cron that quietly died is no help.

Two things here are easy to get wrong and are what most of these tests are for:

1. **Background work.** A request-level dependency looks like enough and is not.
   A mission started before the flip keeps running in a background task for
   minutes afterwards, making model call after model call on no request path at
   all. The gate therefore sits at the provider layer, where the money is
   actually spent.
2. **Not looking like an outage.** Signing in, reading data you already have,
   and the operator console must keep working. A switch that makes the app
   appear crashed is one nobody dares flip, and the point is to stop spend, not
   to lock the owner out while they work out what happened.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture
def ent(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.paths, app.entitlements
    importlib.reload(app.paths)
    e = importlib.reload(app.entitlements)
    e._reset_for_tests()
    return e


ACCT = "acct-under-test"


# ── the flag itself ──────────────────────────────────────────────────────────

def test_an_untouched_account_may_spend(ent):
    """Default ON. The switch is an intervention; nobody has intervened."""
    assert ent.status(ACCT)["killswitch"] is False
    ent.check(ACCT)          # must not raise


def test_flipping_it_stops_the_very_next_check(ent):
    """THE definition of done. No restart, no reload, no cron."""
    ent.check(ACCT)
    ent.set_killswitch(ACCT, True, "runaway spend on the trial key")
    with pytest.raises(ent.Killswitched) as ei:
        ent.check(ACCT)
    assert "runaway spend" in str(ei.value)


def test_it_can_be_switched_back_on(ent):
    ent.set_killswitch(ACCT, True, "pausing")
    with pytest.raises(ent.Killswitched):
        ent.check(ACCT)
    ent.set_killswitch(ACCT, False)
    ent.check(ACCT)          # must not raise


def test_the_reason_survives_so_the_refusal_can_say_something_true(ent):
    ent.set_killswitch(ACCT, True, "card expired")
    try:
        ent.check(ACCT)
        pytest.fail("expected Killswitched")
    except ent.Killswitched as e:
        assert e.reason == "card expired"


def test_a_refusal_without_a_reason_still_says_something_useful(ent):
    ent.set_killswitch(ACCT, True, "")
    try:
        ent.check(ACCT)
        pytest.fail("expected Killswitched")
    except ent.Killswitched as e:
        assert "paused by its operator" in e.reason


def test_it_is_per_account_not_global(ent):
    """Stopping one tenant must not stop another. True before M3 makes it
    reachable, because retrofitting per-account onto a global flag is exactly
    the migration this codebase keeps paying for."""
    ent.set_killswitch("acct-a", True, "only this one")
    with pytest.raises(ent.Killswitched):
        ent.check("acct-a")
    ent.check("acct-b")      # must not raise


def test_the_flag_is_read_from_disk_not_cached(ent, tmp_path):
    """A cache means "flip it" becomes "flip it, eventually"."""
    import sqlite3
    ent.check(ACCT)
    # Write straight to the database, bypassing set_killswitch entirely.
    con = sqlite3.connect(str(tmp_path / "entitlements.db"))
    con.execute("INSERT INTO entitlements (account_id, killswitch, reason, updated_at)"
                " VALUES (?,1,'out of band',0)", (ACCT,))
    con.commit(); con.close()
    with pytest.raises(ent.Killswitched):
        ent.check(ACCT), "the check served a cached value"


def test_the_console_can_list_what_is_stopped(ent):
    ent.set_killswitch("acct-a", True, "one")
    ent.set_killswitch("acct-b", False)
    stopped = {r["account_id"] for r in ent.all_switched_off()}
    assert stopped == {"acct-a"}


# ── the provider-layer gate ──────────────────────────────────────────────────

@pytest.fixture
def app_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MARK4_DEV_ACCOUNT", raising=False)
    import app.paths, app.entitlements, app.accounts, app.auth, app.main
    importlib.reload(app.paths)
    e = importlib.reload(app.entitlements)
    e._reset_for_tests()
    importlib.reload(app.accounts)
    importlib.reload(app.auth)
    return importlib.reload(app.main), e


def test_the_model_provider_refuses_while_stopped(app_mod, monkeypatch):
    """The gate that covers background work.

    A mission already running is on no request path, so only a check at the
    point of spend can stop it.
    """
    _, e = app_mod
    from app.accounts import SINGLE_TENANT
    from app.providers.dispatch import OpenRouterProvider

    e.set_killswitch(SINGLE_TENANT.id, True, "stop spending")
    p = OpenRouterProvider("sk-fake")

    with pytest.raises(e.Killswitched):
        asyncio.run(p.complete([{"role": "user", "content": "hi"}], model="m"))

    async def drain():
        async for _ in p.stream([{"role": "user", "content": "hi"}], model="m"):
            pass
    with pytest.raises(e.Killswitched):
        asyncio.run(drain())

    with pytest.raises(e.Killswitched):
        asyncio.run(p.generate_media([{"role": "user", "content": "hi"}],
                                     model="m", modalities=["image", "text"]))


def test_embeddings_refuse_while_stopped(app_mod, monkeypatch):
    """A vault re-index is the largest single burst of spend the app can make."""
    _, e = app_mod
    from app.accounts import SINGLE_TENANT
    from app.providers import embeddings as E

    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    E = importlib.reload(E)
    e.set_killswitch(SINGLE_TENANT.id, True, "stop spending")

    with pytest.raises(E.EmbedError) as ei:
        asyncio.run(E.embed_texts(["some text"]))
    assert "refused" in str(ei.value)


def test_spending_resumes_immediately_when_switched_back(app_mod, monkeypatch):
    _, e = app_mod
    from app.accounts import SINGLE_TENANT
    from app.providers.dispatch import OpenRouterProvider

    e.set_killswitch(SINGLE_TENANT.id, True, "pause")
    p = OpenRouterProvider("sk-fake")
    with pytest.raises(e.Killswitched):
        asyncio.run(p.complete([{"role": "user", "content": "hi"}], model="m"))

    e.set_killswitch(SINGLE_TENANT.id, False)
    # Now it gets PAST the gate and fails on the network instead, which is the
    # proof it is no longer being refused.
    with pytest.raises(Exception) as ei:
        asyncio.run(p.complete([{"role": "user", "content": "hi"}], model="m"))
    assert not isinstance(ei.value, e.Killswitched), "still refused after resuming"


# ── it must not look like an outage ──────────────────────────────────────────

def test_a_stopped_deployment_answers_403_with_the_reason_not_500(app_mod):
    """500 reads as "we broke" and sends the owner hunting a bug."""
    main, e = app_mod
    from fastapi import HTTPException  # noqa: F401
    from starlette.requests import Request

    handler = main.app.exception_handlers.get(e.Killswitched)
    assert handler is not None, "Killswitched has no handler; it would 500"
    scope = {"type": "http", "method": "GET", "path": "/chat", "headers": []}
    resp = asyncio.run(handler(Request(scope), e.Killswitched("card expired")))
    assert resp.status_code == 403
    body = bytes(resp.body).decode()
    assert "card expired" in body
    assert "killswitch" in body


def test_signing_in_still_works_while_stopped(app_mod):
    """Locking the owner out of their own paused instance would be perverse."""
    main, e = app_mod
    from app.accounts import SINGLE_TENANT
    from fastapi.testclient import TestClient

    e.set_killswitch(SINGLE_TENANT.id, True, "paused")
    c = TestClient(main.app)
    assert c.get("/health").status_code == 200
    assert c.get("/auth/status").status_code == 200


def test_the_console_still_reports_while_stopped(app_mod):
    """The one screen that explains WHY it stopped must not be stopped too."""
    main, e = app_mod
    from app.accounts import SINGLE_TENANT
    from fastapi.testclient import TestClient

    e.set_killswitch(SINGLE_TENANT.id, True, "paused deliberately")
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {"/admin/diagnostics"})
    body = TestClient(main.app).get("/admin/diagnostics").json()

    ks = next(s for s in body["subsystems"] if s["id"] == "killswitch")
    assert ks["status"] == "blocked"
    assert "paused deliberately" in ks["detail"]
    assert ks["unblock"], "a stopped deployment must say how to resume"


def test_the_console_reports_ok_when_not_engaged(app_mod):
    """Armed-and-not-engaged is healthy, not a missing feature."""
    main, _ = app_mod
    from fastapi.testclient import TestClient
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {"/admin/diagnostics"})
    body = TestClient(main.app).get("/admin/diagnostics").json()
    ks = next(s for s in body["subsystems"] if s["id"] == "killswitch")
    assert ks["status"] == "ok"
    assert ks["unblock"] is None


# ── the account seam ─────────────────────────────────────────────────────────

def test_no_session_still_refuses_to_resolve_an_account(app_mod):
    """A missing session must never quietly become somebody."""
    from app.accounts import NotAuthenticated, resolve_account
    with pytest.raises(NotAuthenticated):
        asyncio.run(resolve_account(None))


def test_require_account_applies_the_entitlement_check(app_mod, monkeypatch):
    """authenticate -> resolve -> entitle, in ONE dependency.

    A gate that must be remembered at every callsite gets forgotten at one of
    them, and the forgotten one is the expensive one.
    """
    _, e = app_mod
    monkeypatch.setenv("MARK4_DEV_ACCOUNT", "1")
    import app.accounts as A
    A = importlib.reload(A)

    acct = asyncio.run(A.require_account(None))
    assert acct.id == A.DEV_ACCOUNT.id

    e.set_killswitch(A.DEV_ACCOUNT.id, True, "nope")
    with pytest.raises(e.Killswitched):
        asyncio.run(A.require_account(None))
