# Infinity

Hosted, multi-tenant AI workspace. Every answer cites its source, nobody sees
what they aren't allowed to see, and no model vendor owns us.

**Read first, in this order:**

1. `AGENTS.md` — the working brief. Locked decisions, invariants, and two
   landmine ledgers. Gitignored; it lives in this workspace only.
2. `DIRECTION.md` — the product thesis. What and why. Settled.
3. `MARK4.md` — the original build plan. Settled on order, but see the ⚠️ rows
   in `AGENTS.md`: several decisions have since been overridden.
4. `docs/MARK3-PUNCHLIST.md` — the previous build's defect list, kept as history.
5. `docs/RUNBOOK.md` — the steps that need a human with a browser login.

## Where this is

**M0 · Repo and pipe — done.** CI builds the real container image, boots it,
and fails the build if `/echo/stream` arrives as one lump instead of word by
word. Verified at 211 ms → 1012 ms across five words.

- [x] `/health` returns 200
- [x] `/echo/stream` arrives incrementally, proven in the container
- [x] CORS is an explicit allowlist, never `*`
- [ ] Deployed to Railway on a public HTTPS URL — needs `docs/RUNBOOK.md` §M0
- [ ] Sentry, with PII scrubbing configured *before* the DSN is set

**M1 · The Lift — in progress.** Mark 3 moved into this repo, behaviour
unchanged, before any spine change. The frontend is across and builds clean.

## Layout

```
backend/app/      the lifted backend + the Mark 4 spine
                  (accounts.py, retrieval.py, orchestrator.py)
backend/reactor/  shared memory/provider package — moved INSIDE backend/ so the
                  container image actually contains it
frontend/         React + Vite SPA
infra/Dockerfile  long-running container. Never serverless: the chat path
                  streams NDJSON and research jobs run for minutes
```

## Run locally

```bash
# backend
cd backend
py -3.12 -m venv .venv && . .venv/Scripts/activate   # Linux/mac: .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend && npm install && npm run dev
```

Verify streaming **from a terminal** — a browser `fetch` that awaits the whole
body cannot tell a stream from a lump:

```bash
curl -N -X POST localhost:8000/echo/stream -H 'content-type: application/json' -d '{}'
```

Words must appear one at a time. If they land together the transport is
buffering and chat will never stream. Fix that before building on it.

## Tests

```bash
cd backend && pytest -v
```

`tests/test_tenancy_leak.py` is **written to fail**. It is the executable
definition of done for retrieval — account B must never retrieve account A's
document. The markers are `strict=True`, so an unexpected pass is reported as a
failure: the suite tells you the moment tenancy lands rather than letting a
stale marker hide it.

Do not delete `test_owner_can_still_retrieve_their_own_document`. Without it the
whole suite goes green the day search breaks and returns nothing — a false
all-clear on the one thing being sold.

## The rule that governs everything

> Every piece of state belongs to exactly one account. No global singletons. Any
> function that reads or writes state takes an explicit `account`. If you cannot
> name the owner of a row, the schema is wrong.

Mark 3 still runs at `C:/Code/reactor-mark-3` and is the reference
implementation. When behaviour here looks wrong, diff against it.
