"""`infinity/auto` is a sentinel, and every route must resolve it before use.

On a stock install the selected model IS `infinity/auto`. `/chat` and
`/chat/stream` resolved it; `/mission/plan` did not — so Mission Control was
broken out of the box for anyone who had not manually picked a model.

The failure was badly disguised. The sentinel contains a slash, and
`provider_for_model` treats any `vendor/model` id as OpenRouter's — so it did
not refuse, it cheerfully routed. OpenRouter then rejected `infinity/auto` as an
unknown model, the route turned that into `502 planner failed`, and the user was
pointed at the planner instead of at the caller that forgot to resolve.

`provider_for_model`'s docstring already said "AUTO_MODEL_ID never reaches here".
It was a comment, and a comment cannot enforce anything — which is the general
lesson: an invariant that only exists in prose is one that has already been
violated somewhere you have not looked.
"""

from __future__ import annotations

import importlib
import inspect

import pytest


@pytest.fixture
def chat_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.providers.chat as c
    return importlib.reload(c)


def test_the_sentinel_is_refused_at_the_provider_boundary(chat_mod):
    """Loudly, and naming the caller's mistake rather than the provider's."""
    with pytest.raises(ValueError) as ei:
        chat_mod.provider_for_model(chat_mod.AUTO_MODEL_ID)
    msg = str(ei.value)
    assert "sentinel" in msg
    assert "resolve" in msg


def test_a_real_namespaced_model_still_routes(chat_mod):
    """The guard must not catch ordinary vendor/model ids."""
    name, _ = chat_mod.provider_for_model("anthropic/claude-sonnet-5")
    assert name in ("openrouter", "none")   # depends on what is connected


def test_the_mission_planner_resolves_before_asking_for_a_provider():
    """The route that had the bug.

    Asserted on the source rather than by calling it, because calling it needs a
    connected provider and a live model — and this is a question about the ORDER
    of two lines, which source is the honest place to check.
    """
    import app.main as m
    # Strip comments first. The fix's own comment mentions provider_for_model,
    # and a naive search found THAT — the same false positive the outbound-HTTP
    # check documents. A prose mention is not a call.
    src = "\n".join(l.split("#", 1)[0]
                    for l in inspect.getsource(m.mission_plan).splitlines())
    resolve_at = src.find("AUTO_MODEL_ID")
    use_at = src.find("provider_for_model")
    assert resolve_at != -1, "the mission planner does not mention the sentinel at all"
    assert use_at != -1
    assert resolve_at < use_at, (
        "the mission planner asks for a provider before resolving the sentinel"
    )


def test_every_route_that_asks_for_a_provider_resolves_the_sentinel_first():
    """A ratchet, because this is a whole CLASS of bug.

    Any future route that takes a caller-supplied model and looks up a provider
    inherits the same failure: on a stock install it receives the sentinel and
    reports a confusing upstream error. The provider-boundary guard now turns
    that into a loud 500 instead of a misleading 502 — but the route should
    never get there.
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Functions that default the model to a fixed constant rather than taking
    # it from a request body cannot receive the sentinel.
    EXEMPT = {
        "_utility_llm":
            "Takes model=None and falls back to the fixed _UTILITY_MODEL "
            "constant; both callers pass no model at all, so a caller-supplied "
            "sentinel cannot reach it.",
    }

    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.dump(fn)
        if "provider_for_model" not in body:
            continue
        # A route that never touches a caller-supplied model is fine.
        if "AUTO_MODEL_ID" in body or "_resolve_auto_for" in body or "_route_auto" in body:
            continue
        if fn.name in EXEMPT:
            continue
        offenders.append(fn.name)

    assert not offenders, (
        "route(s) resolving a provider without handling the auto sentinel: "
        + ", ".join(offenders)
        + "\nOn a stock install the selected model IS the sentinel, so these "
          "fail out of the box with an error that blames the provider."
    )


def test_the_resolver_returns_a_concrete_model():
    import app.main as m
    got = m._resolve_auto_for("plan my week")
    assert got and got != m.chat_provider_registry.AUTO_MODEL_ID
    assert "/" in got, "a resolved model should be a real vendor/model id"
