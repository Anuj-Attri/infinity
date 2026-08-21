# MARK 4 — build plan

*Companion to `DIRECTION.md` (from Mark 3), which stays canon for **what** this
product is and **why**. This file is **how** and **in what order**. Where they
disagree about product, DIRECTION wins. Where they disagree about build order,
this file wins.*

---

## 0. What this is, and what it is not

**This is a PORT ONTO A NEW SPINE, not a from-scratch rewrite.**

The deciding argument for rebuilding at all: `account_id` appears **zero times**
in the Mark 3 backend, and per-user permission enforcement is the entire
product. Retrofitting an ownership invariant into ~3,900 lines that never had
one is the most reliable way to ship a permission leak, and a leak is the one
bug that ends the business. That risk exceeds rebuild risk. Nothing else on the
blocker list would justify this on its own.

What is genuinely portable — and must be reused, not reinvented: the design
system and aesthetic, the tile components, the SSRF-hardened server-side
browser, the MCP client/stdio bridge, the consent gate, the Nova persona, the
chain engine, Monaco. What is being replaced is only the spine: auth, storage,
state ownership, and `main.py`.

### The three ways this fails — read before starting

1. **Scope creep.** M0–M4 *is* the product. Everything after is optional. If a
   task does not serve the two-account demo, it is later.
2. **Re-stepping solved bugs.** §9 is the ledger. Read it before writing the
   subsystem it covers, not after the bug reappears.
3. **Losing the working app.** **Mark 3 stays running and untouched** as the
   reference implementation. Do not delete it, do not "migrate" it. When a Mark
   4 behaviour looks wrong, diff against Mark 3.

---

## 1. Inherited canon (do not relitigate)

From `DIRECTION.md`:

- **Three product laws.** Every claim carries a source link. Permissions are
  enforced per-user **at retrieval**. It says "I don't know" — refusal beats
  fabrication.
- **Account ownership.** Every row belongs to exactly one account. No global
  singletons. Every state function takes an explicit `account_id`.
- **Three scopes.** `personal | department | company`. Privacy comes from **key
  custody, not physical location**.
- **Deny by default.** Entitlement is a check inside the auth dependency chain,
  evaluated per request that costs money. Never a cron.
- **Buy the undifferentiated layer.** Auth, billing, entitlements, hosting.
  Retrieval and the permission model stay in-house.
- **Milestones, not timelines.** Every unit of work has a definition of done a
  third party could verify.
- **The demo is the pitch.** Two accounts, same company, same question — one
  cited answer, one "I don't have access to that."

Decided since: **billing plumbing is wired but charging is OFF**; access is
**invite-gated via a website form**; the name stays "Infinity" as a working
codename; local models are cut and return later as an on-prem tier.

---

## 2. Locked architecture

| Layer | Choice | Note |
|---|---|---|
| DB / auth / storage / vectors | **Supabase** (Postgres + RLS + pgvector) | We own the `accounts` table; provider id lives only in `external_auth_id` |
| Backend | **FastAPI in a container on Railway** | Long-running — required for NDJSON streaming and multi-minute jobs |
| Frontend | **React + Vite SPA on Vercel** | Web-first. Tauri becomes an optional wrapper later, never the primary target |
| Models | **OpenRouter only**, zero-data-retention enforced per account and per request | Architect provider failover |
| Billing | **Stripe + Autumn**, wired, disabled | Flip is config, not refactor |
| Errors | **Sentry**, with PII scrubbing enabled *before* it is switched on | |

Portability is the insurance policy: Postgres + a Docker container relocates to
AWS/Azure in an afternoon if an enterprise demands it.

---

## 3. The schema — ownership from row zero

Every table carries an owner from its first migration. There is no "add tenancy
later" step, because that step is the bug.

```sql
-- Tenants ------------------------------------------------------------------
create table orgs (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  created_at    timestamptz not null default now()
);

-- We own this table. The auth provider's id lives ONLY in external_auth_id,
-- so Supabase Auth can be swapped for WorkOS without touching anything else.
create table accounts (
  id                uuid primary key default gen_random_uuid(),
  external_auth_id  text unique,
  org_id            uuid not null references orgs(id),
  email             text,
  created_at        timestamptz not null default now()
);

create type scope_t as enum ('personal','department','company');

create table departments (
  id      uuid primary key default gen_random_uuid(),
  org_id  uuid not null references orgs(id),
  name    text not null
);

create table memberships (
  account_id     uuid not null references accounts(id),
  department_id  uuid not null references departments(id),
  role           text not null default 'member',
  primary key (account_id, department_id)
);

-- Knowledge ----------------------------------------------------------------
create table documents (
  id                 uuid primary key default gen_random_uuid(),
  org_id             uuid not null references orgs(id),
  owner_account_id   uuid not null references accounts(id),
  scope              scope_t not null,
  department_id      uuid references departments(id),   -- required iff scope='department'
  title              text not null,
  source_url         text,                              -- THE CITATION. Not optional in spirit.
  created_at         timestamptz not null default now()
);

create table chunks (
  id                 uuid primary key default gen_random_uuid(),
  document_id        uuid not null references documents(id) on delete cascade,
  -- Denormalised on purpose: retrieval must filter without a join, and the
  -- RLS policy must be evaluable on this row alone.
  org_id             uuid not null references orgs(id),
  owner_account_id   uuid not null references accounts(id),
  scope              scope_t not null,
  department_id      uuid references departments(id),
  content            text not null,
  embedding          vector(1536) not null,             -- FREEZE THIS DIMENSION (§9)
  ord                int not null
);
create index on chunks using hnsw (embedding vector_cosine_ops);
create index on chunks (org_id, scope);

-- Operations ---------------------------------------------------------------
create table turns        (id uuid primary key, account_id uuid not null references accounts(id), ...);
create table usage_events (id uuid primary key, account_id uuid not null references accounts(id), ...);
create table entitlements (account_id uuid primary key references accounts(id),
                           plan text not null default 'beta',
                           trial_ends_at timestamptz,
                           killswitch boolean not null default false);
create table invites      (code text primary key, email text, used_by uuid references accounts(id),
                           used_at timestamptz, created_at timestamptz not null default now());
```

**RLS on every table, in the same migration that creates it.** A table without a
policy is a leak waiting for a careless query.

### Encryption — and the limit of the claim (read this before writing marketing)

Per-org envelope encryption: one data key per org, wrapped by a KMS-held key;
personal-scope rows sealed under a per-user key an org key cannot open. That
delivers "one key locks the company's data."

**But be precise about what is true.** Embeddings must stay queryable to do
server-side vector search, so they sit in the database in a usable form, and
embeddings leak information about their source text. Therefore the honest claim
is *"your content is encrypted at rest under your organisation's key; access is
audited; we do not train on it and enforce zero-data-retention with the model
provider."* The claim *"it is mathematically impossible for us to read your
data"* would require client-side encryption and client-side search, which would
delete the product. **Do not ship the stronger sentence.**

---

## 4. The retrieval rule — this function *is* the product

Every retrieval is scoped before the model is ever called. Filtering after
generation is redaction, not permission, and it leaks through summaries and
citations.

```python
async def retrieve(question: str, *, account: Account, k: int = 8) -> list[Chunk]:
    """The ONE retrieval path. There is no unscoped variant, not even for admin.

    Returns chunks the caller is entitled to read and nothing else:
      org_id = account.org_id
      AND ( scope = 'company'
            OR (scope = 'personal'   AND owner_account_id = account.id)
            OR (scope = 'department' AND department_id IN account.department_ids) )
    """
```

Two layers, always, because either alone fails open:

- **Application layer** — the predicate above, in the query. Fast, testable, and
  the thing the leak test exercises.
- **RLS** — the same rule as policy, so one forgotten `WHERE` cannot become a
  breach.

> **The footgun that silently deletes this entire design:** Supabase's
> `service_role` key **bypasses RLS completely**, and a FastAPI backend is
> exactly the thing that connects with `service_role`. Write perfect policies,
> deploy, and they enforce nothing — while the dashboard reads "RLS enabled."
> Either forward the user's JWT so policies evaluate per request, or treat RLS
> strictly as a backstop and make the application predicate mandatory. Never
> assume RLS is running because it is switched on.

---

## 5. Repo layout

```
backend/
  app/
    accounts.py        # PORT FROM MARK 3 — already written, already correct
    orchestrator.py    # run_turn(account, message, ctx) — the ONE entry point
    retrieval.py       # the function in §4; no unscoped variant exists
    vault.py           # ingest, chunk, embed
    browse.py          # PORT AS-IS — SSRF-hardened, portable unchanged
    ocr.py             # PORT AS-IS — scanned-PDF fallback for ingestion
    mcp/               # PORT — client, stdio bridge, consent gate
    routers/           # thin adapters: parse -> resolve account -> run_turn
    entitlements.py    # deny-by-default check in the auth dependency chain
  tests/
    test_tenancy_leak.py   # PORT FROM MARK 3 — the acceptance gate
  migrations/
frontend/              # React + Vite SPA (web-first)
  src/styles/          # PORT — the design system, unchanged
  src/components/      # PORT — tiles, palette, chat
infra/
  Dockerfile
```

**No file may reach into another account's data. If a function touches state and
does not take an `account`, it is wrong — no defaults, no module-level "current
user," no `Optional[Account] = None`.**

---

## 6. Milestones

Each has a definition of done a third party could verify. No dates.

**M0 · Skeleton, deployed.** Repo, Dockerfile, FastAPI on Railway, empty
Supabase, SPA shell on Vercel, real HTTPS domain, Sentry with scrubbing on.
Include one **streaming echo endpoint**.
*Done when:* the public URL returns healthy JSON, the frontend loads over HTTPS,
and the echo endpoint streams token-by-token through the proxy (not buffered
into one lump). CORS locked to the known origins — never `*`.
*Why first:* it converts every deployment unknown into a known while nothing
rides on it, so later milestones debug **one** unfamiliar system at a time.

**M1 · Identity.** `orgs`/`accounts`/`memberships`, Supabase Auth, our own
accounts row keyed by `external_auth_id`, invite-gate backed by `invites`.
*Done when:* two different people sign up through the form, each lands on a
distinct `accounts` row in the same org, and a request without a valid session
is refused.

**M2 · The spine.** `orchestrator.run_turn(account, message, ctx)` as the single
entry point; routers are thin adapters. Chat works end to end on the deployed
app with streaming.
*Done when:* a pytest calls `run_turn` **with no FastAPI**, and a real turn
streams in the browser against the hosted backend.

**M3 · Retrieval with permissions.** `documents`/`chunks` with owner columns,
pgvector, the §4 function, RLS policies live.
*Done when:* **`test_tenancy_leak.py` passes — all of it**, including the
not-vacuous guard, with RLS enabled and against the real database.

**M4 · Citations and refusal.** Every answer carries source links resolved from
`documents.source_url`; empty retrieval produces "I don't have access to that"
rather than a plausible number.
*Done when:* **the two-account demo runs live** — same org, same question, one
cited answer, one refusal.

*M0–M4 is the product. Ship the beta here.*

**M5 · Surface.** Port tiles and chat UI, responsive web pass, real command
palette, lazy-load Monaco (the 1.72 MB chunk is invisible on desktop and a
multi-second blank screen on the web).
*Done when:* a stranger goes from signup to a useful cited answer with no
instructions, and first paint is not blocked by the editor.

**M6 · Money plumbing, off.** Entitlement check in the auth dependency chain,
per-account metering, Stripe + Autumn wired, charging disabled.
*Done when:* a trial marked expired refuses the next paid request while you are
asleep, and enabling billing is a config change with no code edit.

**M7 · Admin.** Spend per user, killswitch toggle, and the "view as" graph —
which earns its place only if it is operational (filter, click a cluster, see
who can read it). If it is only beautiful, it is the deleted Vision tile again.

---

## 7. Port / rebuild / drop

**Port as-is** — proven, portable, expensive to rediscover:
`accounts.py`, `test_tenancy_leak.py`, `browse.py` (SSRF-hardened), `ocr.py`,
MCP client + stdio bridge + the consent gate classifier, the Nova persona core
and its separate `OUTPUT_HYGIENE` addendum, the chain engine and artifact slugs,
the whole design system (`supernova.css` tokens), tile components, Monaco
`CodeTile`.

**Rebuild** — the spine, and the reason for Mark 4:
`main.py` (3,855 lines / 52 inline endpoints) → routers + `run_turn`;
`auth.py` (master password + device tokens) → Supabase Auth;
DPAPI keystore → env + KMS envelope encryption;
~20 files of `localStorage`-as-source-of-truth → server state per account;
`.data/*.json` + six SQLite files → Postgres with owner columns.

**Drop:** Tauri as the primary target, the trader (liability — paper/read-only
at most, and not in the beta), the local-model brain (returns as the on-prem
tier), and everything already cut in Mark 3 (Spotify, camera/YOLO, 3D graph,
quote, youtube, sensors, animate).

---

## 8. Rules for the agent

1. `account` is a **required** parameter on every state-touching function. No
   default, no global, no `None`.
2. A migration that creates a table **also** creates its RLS policy.
3. `test_tenancy_leak.py` is the gate. It may not regress. A change that makes
   it pass for the wrong reason (retrieval returning nothing) is caught by the
   not-vacuous test — do not delete that test.
4. **Verify on the deployed app**, not on `tsc --noEmit`. "It compiles" has
   never once meant "it works" in this project's history.
5. Do not rebuild Monaco, the design system, or `browse.py`.
6. Never let a failure be silent: no `let _ = ...`, no swallowed exceptions, no
   truncation without a flag. See §9 — most of that ledger is silent failures.
7. Milestones, not timelines. Every PR states its definition of done.
8. Mark 3 stays running. Diff against it when behaviour looks wrong.

---

## 9. Landmine ledger — solved once, do not solve again

Each of these cost real debugging time in Mark 3.

**Python / FastAPI**
- With `from __future__ import annotations`, **every name in a Pydantic field
  type must be importable in that module**, or you get a `py_compile`-clean
  **runtime 500 at validation** — before your handler body runs. A missing
  `from typing import Any` cost hours. Isolated probes and compile checks both
  miss it; only the live error log shows it.
- Code changes need the service **restarted**; frontend changes need a
  **rebuild**. A large share of "the fix didn't work" was stale code.

**Models / LLM lanes**
- **Reasoning tokens eat the visible answer.** Two separate incidents: thinking
  mode returning blank replies (everything went to `reasoning_content`), and
  length-truncation mid-sentence. Budget reasoning **and** output, and always
  emit a `finish_reason == "length"` flag. Truncation must never be silent.
- A fixed `n_predict` sitting under a larger context budget cuts replies
  mid-word. Keep generation bounded by EOS/context, not an arbitrary constant.
- **Grammar-constrained tool calls:** a lazy grammar trigger ignored by the chat
  endpoint forced *every* reply into a tool call. The working shape was
  render-template + completion, plus a contrastive few-shot so a small model
  stops wrapping ordinary answers in invented actions.
- **Do not clamp temperature** to "improve rigor" — it is a structural
  sycophancy re-flattener that sands off the persona.
- Small models flatten persona and nuance. Route opinion/judgment turns to the
  strong model; keep the cheap lane for mechanical work.

**Frontend / web**
- `react-markdown` v9's `defaultUrlTransform` **strips custom schemes**, which
  produced an empty `href`, a real navigation, an SPA remount, and a wiped
  session. Custom link schemes need an explicit `urlTransform`.
- A streaming tool-call block must be hidden **the moment its opening tag
  appears** and buffered until complete; otherwise raw JSON flashes into the
  transcript and a partial block never becomes a card.
- Any reply-length clamp must **exclude** tool-call blocks, or it severs them
  and the parser silently dies.
- `navigator.clipboard` **does not exist on a plain-http origin** — it is not a
  secure context. The same rule kills `getUserMedia` (camera/mic). Serve HTTPS
  from day one and this whole class disappears.
- **`visibilitychange` does not fire on Android background.** Use the platform
  app-state event.
- **Non-interactive ≠ non-animating.** An idle animation loop at ~14 fps cooked
  a phone for weeks. Anything animated must settle to static when idle and hard
  pause when hidden or streaming. `backdrop-filter` is the most expensive mobile
  compositing operation there is.
- Never hardcode a colour in a themed app — a white `#fff` panel in a dark UI is
  how the command palette shipped wrong.

**Infra**
- **`service_role` bypasses RLS.** See §4. This is the one that would quietly
  turn the product into a demo.
- **Supabase free projects pause after 7 days idle** — fatal for an invite beta
  where testers return a week later to a dead app. Pro before external testers.
- **Streaming dies behind buffering proxies.** Long-running container, verified
  at M0, never serverless for the chat path.
- **Embedding dimensions are baked into the column.** Changing embedding models
  later means re-embedding everything. Choose once, freeze, record the choice.
- Egress overage is the surprise line on a Supabase bill, not compute.

---

## 10. First session in Cursor

1. Read `DIRECTION.md` (product) and this file (build). Treat both as settled.
2. Scaffold the repo in §5 and complete **M0**. Nothing else.
3. Port `accounts.py` and `test_tenancy_leak.py` from Mark 3 **first**, before
   any feature code, so the target exists before the thing that satisfies it.
4. Report M0's definition of done as evidence — a URL, a streamed response, a
   green health check — not "it builds."
