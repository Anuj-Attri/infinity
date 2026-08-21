# Infinity

Multi-tenant AI workspace. FastAPI backend, React SPA, Postgres with per-account
row ownership.

## Run

```bash
# backend  →  http://127.0.0.1:8000
cd backend
py -3.12 -m venv .venv && . .venv/Scripts/activate   # linux/mac: .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# frontend →  http://127.0.0.1:5173
cd frontend && npm install && npm run dev
```

## Test

```bash
cd backend && pytest                  # incl. the security gate (see docs/PORTING.md)
cd frontend && npm run typecheck
cd frontend && npm run smoke          # every tile opens, in a real browser
cd frontend && node e2e/functional.mjs
```

CI runs all of it against the built container image, because several bugs have
existed **only** in the image — a `.dockerignore` rule that excluded the
frontend, and one that nearly ate the skills content.

Streaming must be checked from a terminal — a browser `fetch` that awaits the
whole body cannot tell a stream from a lump:

```bash
curl -N -X POST localhost:8000/echo/stream -H 'content-type: application/json' -d '{}'
```

Words arrive one at a time, or the transport is buffering.

## The rule

> Every piece of state belongs to exactly one account. No global singletons. Any
> function that reads or writes state takes an explicit `account`. If you cannot
> name the owner of a row, the schema is wrong.

`tests/test_tenancy_leak.py` is written to fail; it is the executable definition
of done for retrieval. Markers are `strict=True`, so an unexpected pass fails the
suite. Do not delete `test_owner_can_still_retrieve_their_own_document` — without
it the suite goes green the day search breaks and returns nothing.

## Docs

| File | |
|---|---|
| `AGENTS.md` | working brief: locked decisions, invariants, landmine ledgers (gitignored) |
| `docs/DIRECTION.md` | product thesis |
| `docs/MARK4.md` | original build plan — see the ⚠️ rows in `AGENTS.md` for what has since changed |
| `docs/PORTING.md` | **how to move a Mark 3 module across safely** — read before porting anything |
| `docs/RUNBOOK.md` | steps needing a browser login |
| `docs/MARK3-PUNCHLIST.md` | previous build's defect list, kept as history |

Mark 3 runs at `C:/Code/reactor-mark-3` and is the reference implementation.
Diff against it when behaviour here looks wrong.
