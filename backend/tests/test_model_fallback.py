"""When the hosted model cannot answer, the user gets a local model or a generic
"model unavailable", never a billing reason and never the provider's name.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INFINITY_DEV_ACCOUNT", "1")
    monkeypatch.delenv("LOCAL_MODEL_URL", raising=False)
    monkeypatch.delenv("LOCAL_MODEL", raising=False)
    monkeypatch.delenv("SPEND_CAP_USD", raising=False)
    import app.paths, app.auth, app.spend, app.entitlements, app.main
    importlib.reload(app.paths)
    importlib.reload(app.auth)
    importlib.reload(app.entitlements)   # else a killswitch from another test leaks in
    importlib.reload(app.spend)
    main = importlib.reload(app.main)
    main._AUTH_EXEMPT = frozenset(set(main._AUTH_EXEMPT) | {"/chat", "/chat/stream"})

    # No embedder here. Other tests reload the embeddings module, after which
    # its error class is no longer the one main catches, so raise main's own.
    async def no_embedder(*a, **k):
        raise main.EmbedError("no embedder in this test")
    monkeypatch.setattr(main, "embed_text", no_embedder)
    return main


class _Refusing:
    """A hosted provider that says no before producing anything."""
    last_finish = None

    def __init__(self, main):
        self.err = main.chat_provider_registry.ProviderError

    async def stream(self, **_):
        raise self.err("402 out of credit")
        yield  # pragma: no cover


class _Working:
    last_finish = "stop"

    async def stream(self, **_):
        yield "hosted reply"


def _hosted(monkeypatch, main, provider):
    monkeypatch.setattr(main.chat_provider_registry, "provider_for_model",
                        lambda m: ("openrouter", provider))


def _local(monkeypatch, main, text="local reply"):
    monkeypatch.setenv("LOCAL_MODEL_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LOCAL_MODEL", "llama3.2")

    async def fake(messages, *, temperature=None):
        yield text
    monkeypatch.setattr(main.local_model, "stream_chat", fake)


def _stream(main) -> list[dict]:
    r = TestClient(main.app).post("/chat/stream", json={"message": "hi", "model": "x/y"})
    assert r.status_code == 200, r.text
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


def _no_leak(lines) -> None:
    text = json.dumps(lines).lower()
    for word in ("openrouter", "credit", "cap", "402", "billing"):
        assert word not in text, f"{word!r} reached the user"


def test_out_of_credit_with_no_local_model_is_a_generic_error(monkeypatch, app_env):
    _hosted(monkeypatch, app_env, _Refusing(app_env))
    lines = _stream(app_env)
    assert lines == [{"error": "Model unavailable right now. Try again later."}]
    _no_leak(lines)


def test_out_of_credit_falls_back_to_the_local_model(monkeypatch, app_env):
    _hosted(monkeypatch, app_env, _Refusing(app_env))
    _local(monkeypatch, app_env)
    lines = _stream(app_env)
    assert lines[0] == {"delta": "local reply"}
    assert lines[-1]["done"] is True
    assert lines[-1]["fallback"] == "local", "a local reply must be labelled"
    assert lines[-1]["model"] == "llama3.2"


def test_the_spend_limit_from_the_environment_applies(monkeypatch, app_env):
    _hosted(monkeypatch, app_env, _Working())
    monkeypatch.setenv("SPEND_CAP_USD", "1")
    app_env.spend.record(provider="openrouter", model="anthropic/claude-opus",
                         in_tok=1_000_000, out_tok=1_000_000)
    lines = _stream(app_env)
    assert lines == [{"error": "Model unavailable right now. Try again later."}]
    _no_leak(lines)


def test_over_the_limit_the_local_model_answers(monkeypatch, app_env):
    _hosted(monkeypatch, app_env, _Working())
    _local(monkeypatch, app_env)
    monkeypatch.setenv("SPEND_CAP_USD", "1")
    app_env.spend.record(provider="openrouter", model="anthropic/claude-opus",
                         in_tok=1_000_000, out_tok=1_000_000)
    lines = _stream(app_env)
    assert lines[0] == {"delta": "local reply"}
    assert lines[-1]["fallback"] == "local"


def test_under_the_limit_the_hosted_model_answers(monkeypatch, app_env):
    _hosted(monkeypatch, app_env, _Working())
    _local(monkeypatch, app_env)
    monkeypatch.setenv("SPEND_CAP_USD", "100")
    lines = _stream(app_env)
    assert lines[0] == {"delta": "hosted reply"}
    assert "fallback" not in lines[-1]


def test_a_cap_set_in_the_app_overrides_the_environment(monkeypatch, app_env):
    monkeypatch.setenv("SPEND_CAP_USD", "1")
    assert app_env.spend.get_cap() == 1.0
    app_env.spend.set_cap(50)
    assert app_env.spend.get_cap() == 50.0


def test_the_non_streaming_endpoint_obeys_the_limit_too(monkeypatch, app_env):
    _hosted(monkeypatch, app_env, _Working())
    monkeypatch.setenv("SPEND_CAP_USD", "1")
    app_env.spend.record(provider="openrouter", model="anthropic/claude-opus",
                         in_tok=1_000_000, out_tok=1_000_000)
    c = TestClient(app_env.app)
    r = c.post("/chat", json={"message": "hi", "model": "x/y"})
    assert r.status_code == 503
    assert r.json()["detail"] == "Model unavailable right now. Try again later."
    _local(monkeypatch, app_env)
    r = c.post("/chat", json={"message": "hi", "model": "x/y"})
    assert r.status_code == 200 and r.json()["reply"] == "local reply"


def test_an_empty_local_reply_is_an_error_not_a_blank_answer(monkeypatch, app_env):
    _hosted(monkeypatch, app_env, _Refusing(app_env))
    monkeypatch.setenv("LOCAL_MODEL_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LOCAL_MODEL", "thinker")

    async def silent(messages, *, temperature=None):
        return
        yield  # pragma: no cover
    monkeypatch.setattr(app_env.local_model, "stream_chat", silent)
    assert _stream(app_env) == [{"error": "Model unavailable right now. Try again later."}]
