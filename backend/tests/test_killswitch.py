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


# ── the gaps a provider-layer gate can still miss ────────────────────────────

def test_the_chat_lane_is_gated(app_mod):
    """The one I missed first time round.

    /chat and /chat/stream go through providers/chat.py, NOT providers/
    dispatch.py — so gating only dispatch left the primary chat path spending
    while the switch was on. Every billing method here, across all four
    providers.
    """
    _, e = app_mod
    from app.accounts import SINGLE_TENANT
    from app.providers.chat import (AnthropicProvider, GeminiProvider,
                                    OpenAIProvider, OpenRouterChatProvider)
    e.set_killswitch(SINGLE_TENANT.id, True, "stop")

    async def drain(p):
        async for _ in p.stream(model="m", messages=[{"role": "user", "content": "hi"}],
                                no_think=False, temperature=0.5):
            pass

    for cls in (AnthropicProvider, OpenAIProvider, GeminiProvider, OpenRouterChatProvider):
        with pytest.raises(e.Killswitched):
            asyncio.run(drain(cls("k")))

    with pytest.raises(e.Killswitched):
        asyncio.run(OpenRouterChatProvider("k").complete_text(
            model="m", messages=[{"role": "user", "content": "hi"}]))


def test_every_billing_method_in_the_provider_modules_is_gated():
    """A ratchet, because the gap above is easy to reintroduce.

    A new provider, or a new method on an existing one, is a new way to spend.
    Nothing about forgetting the gate looks wrong — the code works, it just
    bills through a switch that is supposed to be off.
    """
    import ast
    from pathlib import Path

    PROVIDERS = Path(__file__).resolve().parents[1] / "app" / "providers"
    BILLING = {"stream", "complete", "complete_text", "generate_media", "embed_texts"}
    ungated: list[str] = []

    for f in sorted(PROVIDERS.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if node.name not in BILLING:
                continue
            # A Protocol/ABC stub has no body to gate.
            body = [n for n in node.body if not isinstance(n, ast.Expr)
                    or not isinstance(getattr(n, "value", None), ast.Constant)]
            if not body or (len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Ellipsis))):
                continue
            calls = {n.func.id for n in ast.walk(node)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            if "assert_may_spend" not in calls:
                ungated.append(f"{f.name}:{node.name} (line {node.lineno})")

    assert not ungated, (
        "billing method(s) with no killswitch check: " + ", ".join(ungated) +
        "\nEvery method that bills an upstream must call assert_may_spend() "
        "first. The gate lives at the provider layer, not on the route, because "
        "background missions are on no request path."
    )


def test_no_route_bills_openrouter_without_going_through_the_gate():
    """/stocks/summary built its own OpenRouter request inline, so it was
    invisible to the provider gate AND to spend.record."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    if "openrouter.ai/api/v1/chat/completions" in src:
        assert "assert_may_spend()" in src, (
            "main.py calls OpenRouter directly but never checks the killswitch"
        )


def test_a_paused_read_does_not_destroy_working_vectors(app_mod, monkeypatch, tmp_path):
    """The nastiest one: a killswitched GET corrupting the index.

    _index_file DELETEs a note's chunks and re-inserts them. /vault/tree,
    /vault/graph and /vault/stats all call scan() first — so merely READING the
    vault while paused would replace every edited note's vectors with nulls.
    """
    _, e = app_mod
    import app.vault as V
    from app.accounts import SINGLE_TENANT
    from app.providers import embeddings as E

    V = importlib.reload(V)
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    E2 = importlib.reload(E)

    import numpy as np

    async def _fake(texts, *, input_type="document"):
        return [np.eye(1024, dtype=np.float32)[i % 1024] for i in range(len(texts))]
    monkeypatch.setattr(E2, "embed_texts", _fake)

    V.VAULT_DIR.mkdir(parents=True, exist_ok=True)
    note = V.VAULT_DIR / "keep.md"
    note.write_text("# Keep\n\nirreplaceable content\n", encoding="utf-8")
    asyncio.run(V.scan(force=True))
    before = V._db().execute(
        "SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
    assert before >= 1

    # Pause spending, edit the file, and read the vault.
    e.set_killswitch(SINGLE_TENANT.id, True, "paused")
    note.write_text("# Keep\n\nirreplaceable content, edited\n", encoding="utf-8")
    asyncio.run(V.scan(force=True))

    after = V._db().execute(
        "SELECT COUNT(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
    assert after == before, (
        "a read during a pause destroyed working vectors — the index was "
        "rebuilt with nulls because the re-embed was refused"
    )


def test_a_refusal_is_distinguishable_from_being_unable(app_mod, monkeypatch):
    """EmbedRefused subclasses EmbedError so existing handlers keep degrading,
    while callers that would DESTROY something can tell the difference."""
    _, e = app_mod
    from app.accounts import SINGLE_TENANT
    from app.providers import embeddings as E

    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    E2 = importlib.reload(E)
    e.set_killswitch(SINGLE_TENANT.id, True, "paused")

    with pytest.raises(E2.EmbedRefused) as ei:
        asyncio.run(E2.embed_texts(["x"]))
    assert isinstance(ei.value, E2.EmbedError), (
        "EmbedRefused must stay an EmbedError or every existing handler breaks"
    )
