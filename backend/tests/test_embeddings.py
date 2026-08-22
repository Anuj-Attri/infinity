"""Embeddings fail loudly or not at all.

Every failure mode this file pins is SILENT if unguarded, which is why they are
worth tests at all. Search that raises gets fixed in an hour; search that
returns a confident ranking of noise gets believed, and it undermines the first
product law — every claim carries a source — because the source was found by
matching against garbage.

The three silent failures:

  1. A ZERO VECTOR on failure. Cosine similarity against zeros is 0 or NaN for
     everything, so results come back ranked and meaningless instead of erroring.
  2. A WRONG DIMENSION. Vectors that cannot be compared with the rest of the
     index, written as though they can.
  3. MIXED MODELS. Two models' vectors at the same dimension still multiply
     cleanly and still produce a ranking. Nothing looks broken. Results are noise.

The network is never touched here: httpx is stubbed, so these run in CI with no
key and no egress.
"""

from __future__ import annotations

import asyncio
import importlib

import numpy as np
import pytest


@pytest.fixture
def emb(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_MODEL", raising=False)
    monkeypatch.delenv("VOYAGE_DIM", raising=False)
    import app.providers.embeddings as e
    return importlib.reload(e)


class _Resp:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


async def _no_sleep(*_a, **_k):
    """Skip backoff waits. Must NOT call asyncio.sleep — the tests patch that
    same attribute, so referring to it here recurses forever."""
    return None


def _stub(monkeypatch, mod, resp):
    """Replace the HTTP round-trip; assert nothing real is dialled."""
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            _stub.last = {"url": url, **kw}
            return resp
    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)


# ── unconfigured ─────────────────────────────────────────────────────────────

def test_unconfigured_raises_and_never_returns_a_vector(emb):
    assert emb.is_configured() is False
    assert emb.active_model() is None and emb.active_dim() is None
    with pytest.raises(emb.EmbedError) as ei:
        asyncio.run(emb.embed_text("anything"))
    assert "VOYAGE_API_KEY" in str(ei.value)


def test_status_says_it_is_off_rather_than_looking_healthy(emb):
    s = emb.status()
    assert s["configured"] is False
    assert s["provider"] is None
    assert "No embedding provider" in s["detail"]


# ── the decision on record ───────────────────────────────────────────────────

def test_the_frozen_model_and_dimension(emb, monkeypatch):
    """voyage-4 at 1024 dims. Changing either invalidates every stored vector."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    import app.providers.embeddings as e
    e = importlib.reload(e)
    assert e.VOYAGE_MODEL == "voyage-4"
    assert e.VOYAGE_DIM == 1024
    assert e.active_model() == "voyage-4" and e.active_dim() == 1024


# ── the happy path, and what it sends ────────────────────────────────────────

def test_a_successful_embed_is_unit_length(emb, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    raw = [float(i % 7) + 1.0 for i in range(e.VOYAGE_DIM)]
    _stub(monkeypatch, e, _Resp(200, {"data": [{"embedding": raw, "index": 0}]}))

    v = asyncio.run(e.embed_text("hello"))
    assert v.dtype == np.float32
    assert v.shape == (e.VOYAGE_DIM,)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5, "must be L2-normalised"


def test_search_and_index_are_embedded_differently(emb, monkeypatch):
    """Voyage embeds a question differently from stored text; using one type
    for both silently degrades every result."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    raw = [1.0] * e.VOYAGE_DIM
    _stub(monkeypatch, e, _Resp(200, {"data": [{"embedding": raw, "index": 0}]}))

    asyncio.run(e.embed_text("q", input_type="query"))
    assert _stub.last["json"]["input_type"] == "query"
    asyncio.run(e.embed_text("d", input_type="document"))
    assert _stub.last["json"]["input_type"] == "document"
    assert _stub.last["json"]["model"] == "voyage-4"
    assert _stub.last["json"]["output_dimension"] == 1024


# ── the silent failures ──────────────────────────────────────────────────────

def test_a_wrong_dimension_is_refused(emb, monkeypatch):
    """A short vector must not be stored as if it were comparable."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    _stub(monkeypatch, e, _Resp(200, {"data": [{"embedding": [0.1] * 768, "index": 0}]}))
    with pytest.raises(e.EmbedError) as ei:
        asyncio.run(e.embed_text("hello"))
    assert "1024" in str(ei.value)


def test_a_zero_vector_from_the_provider_is_refused(emb, monkeypatch):
    """The exact thing that turns search into confident noise."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    _stub(monkeypatch, e, _Resp(200, {"data": [{"embedding": [0.0] * e.VOYAGE_DIM, "index": 0}]}))
    with pytest.raises(e.EmbedError):
        asyncio.run(e.embed_text("hello"))


def test_a_non_finite_vector_is_refused(emb, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    bad = [float("nan")] * e.VOYAGE_DIM
    _stub(monkeypatch, e, _Resp(200, {"data": [{"embedding": bad, "index": 0}]}))
    with pytest.raises(e.EmbedError):
        asyncio.run(e.embed_text("hello"))


def test_a_short_batch_response_is_refused(emb, monkeypatch):
    """Two texts in, one vector back: guessing the pairing would mislabel data."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    _stub(monkeypatch, e, _Resp(200, {"data": [{"embedding": [1.0] * e.VOYAGE_DIM, "index": 0}]}))
    with pytest.raises(e.EmbedError) as ei:
        asyncio.run(e.embed_texts(["a", "b"]))
    assert "refusing to guess" in str(ei.value)


def test_results_are_ordered_by_the_index_the_provider_returns(emb, monkeypatch):
    """Do not assume response order matches request order."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    first = [1.0] + [0.0] * (e.VOYAGE_DIM - 1)
    second = [0.0, 1.0] + [0.0] * (e.VOYAGE_DIM - 2)
    # Deliberately out of order.
    _stub(monkeypatch, e, _Resp(200, {"data": [
        {"embedding": second, "index": 1},
        {"embedding": first, "index": 0},
    ]}))
    a, b = asyncio.run(e.embed_texts(["a", "b"]))
    assert a[0] == pytest.approx(1.0), "first result is not the first input"
    assert b[1] == pytest.approx(1.0)


def test_a_rejected_key_fails_immediately_without_retrying(emb, monkeypatch):
    """401 will still be 401 in four seconds; retrying only delays the answer."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    tries = {"n": 0}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            tries["n"] += 1
            return _Resp(401)
    monkeypatch.setattr(e.httpx, "AsyncClient", _C)

    with pytest.raises(e.EmbedError) as ei:
        asyncio.run(e.embed_text("hello"))
    assert "rejected" in str(ei.value)
    assert tries["n"] == 1, "a bad key was retried"


def test_a_rate_limit_is_retried_and_then_succeeds(emb, monkeypatch):
    """THE BUG THIS FIXES. A new Voyage account has a low per-minute cap, so
    ingesting three notes in a row left one vector between them — every embed
    after the first few gave up on a 429 and the chunk was stored unsearchable."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    monkeypatch.setattr(e.asyncio, "sleep", _no_sleep)
    good = {"data": [{"embedding": [1.0] * e.VOYAGE_DIM, "index": 0}]}
    seq = [_Resp(429), _Resp(429), _Resp(200, good)]
    tries = {"n": 0}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            r = seq[tries["n"]]
            tries["n"] += 1
            return r
    monkeypatch.setattr(e.httpx, "AsyncClient", _C)

    v = asyncio.run(e.embed_text("hello"))
    assert v.shape == (e.VOYAGE_DIM,)
    assert tries["n"] == 3, "did not retry through the rate limit"


def test_a_persistent_rate_limit_eventually_raises(emb, monkeypatch):
    """Retrying forever would hang a request instead of answering."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    monkeypatch.setattr(e.asyncio, "sleep", _no_sleep)
    tries = {"n": 0}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            tries["n"] += 1
            return _Resp(429)
    monkeypatch.setattr(e.httpx, "AsyncClient", _C)

    with pytest.raises(e.EmbedError) as ei:
        asyncio.run(e.embed_text("hello"))
    assert tries["n"] == e._MAX_RETRIES
    assert "reindex" in str(ei.value), "the error should say how to recover"


def test_a_server_error_is_retried_too(emb, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    monkeypatch.setattr(e.asyncio, "sleep", _no_sleep)
    good = {"data": [{"embedding": [1.0] * e.VOYAGE_DIM, "index": 0}]}
    seq = [_Resp(503), _Resp(200, good)]
    tries = {"n": 0}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            r = seq[tries["n"]]; tries["n"] += 1; return r
    monkeypatch.setattr(e.httpx, "AsyncClient", _C)
    asyncio.run(e.embed_text("hello"))
    assert tries["n"] == 2


def test_a_bad_request_is_NOT_retried(emb, monkeypatch):
    """400 means the payload is wrong; sending it four more times cannot help."""
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    tries = {"n": 0}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            tries["n"] += 1
            return _Resp(400, None, text="unknown model")
    monkeypatch.setattr(e.httpx, "AsyncClient", _C)
    with pytest.raises(e.EmbedError) as ei:
        asyncio.run(e.embed_text("hello"))
    assert tries["n"] == 1
    assert "unknown model" in str(ei.value), "the provider's own reason was dropped"


def test_retry_after_is_honoured_when_the_server_sends_one(emb, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    assert e._retry_after(_Resp(429, headers={"retry-after": "5"})) == 5.0
    assert e._retry_after(_Resp(429, headers={})) is None
    assert e._retry_after(_Resp(429, headers={"retry-after": "Tue, 1 Jan 2030"})) is None
    # Refuse to block for minutes on a single embed.
    assert e._retry_after(_Resp(429, headers={"retry-after": "600"})) is None


def test_an_empty_string_is_refused_rather_than_zero_filled(emb, monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    e = importlib.reload(emb)
    with pytest.raises(e.EmbedError):
        asyncio.run(e.embed_text("   "))


def test_the_old_entrypoint_still_works_and_still_fails_closed(emb):
    """Six call sites import reactor.memory.embed; it must keep the contract."""
    from reactor.memory import embed as shim
    importlib.reload(shim)
    assert shim.EMBED_DIM == 1024
    with pytest.raises(shim.EmbedError):
        asyncio.run(shim.embed_text("x"))
