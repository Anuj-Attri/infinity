# Mark 4

Hosted, multi-tenant AI workspace. Every answer cites its source, nobody sees
what they aren't allowed to see, and no model vendor owns us.

**Read first, in this order:**

1. `DIRECTION.md` — the product thesis. What and why. Settled.
2. `MARK4.md` — the build plan. How and in what order. Settled.
3. `docs/MARK3-PUNCHLIST.md` — the previous build's defect list, kept as history.

## Current milestone: **M0 — skeleton, deployed**

M0 exists to prove the pipe before anything rides on it. Its definition of done:

- [ ] `/health` returns 200 on the public HTTPS URL
- [ ] `/echo/stream` arrives **incrementally**, not as one lump at the end
- [ ] CORS is an explicit allowlist (never `*`)
- [ ] Frontend shell loads over HTTPS
- [ ] Sentry on, with PII scrubbing enabled first

Do not start M1 until every box is ticked with evidence.

## Run locally

```bash
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --reload --port 8000
```

Verify streaming **from a terminal** — a browser `fetch` that awaits the whole
body cannot tell a stream from a lump:

```bash
curl -N -X POST localhost:8000/echo/stream -H 'content-type: application/json' -d '{}'
```

Words must appear one at a time. If they all land at once, the transport is
buffering and the chat path will never stream. Fix that before building on it.

## Tests

```bash
cd backend && pytest -v
```

`tests/test_tenancy_leak.py` is **written to fail**. It is the executable
definition of done for M3 — account B must never retrieve account A's document.
The markers are `strict=True`, so an unexpected pass is reported as a failure:
the suite tells you the moment tenancy lands. Do not delete the "owner can still
read their own document" guard — without it the whole suite passes trivially the
day retrieval breaks.

## The rule that governs everything

> Every piece of state belongs to exactly one account. No global singletons. Any
> function that reads or writes state takes an explicit `account`. If you cannot
> name the owner of a row, the schema is wrong.

Mark 3 is still running and is the reference implementation. When behaviour here
looks wrong, diff against it.
