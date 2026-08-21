# DIRECTION — what this software is becoming

*Product direction, not a task list. PUNCHLIST.md holds the open defects;
ROADMAP.md and FEATURES.md hold the older single-user plan and should be read
as history where they conflict with this file. This file wins.*

---

## The one-line product

**An AI workspace a company can actually trust: every answer cites its source,
nobody sees what they aren't allowed to see, and no model vendor owns you.**

## Who it is for, in order

1. **Now — the individual.** A paid personal tier any stranger can sign up for,
   hosted, that works while they're away. This is the beta and the first revenue.
2. **Next — the small tech company (5–50 people).** Their knowledge is scattered
   across Slack, repos and docs; they have no IT department to fight; their
   options today are Copilot (widely hated), Glean (priced for 1000+ seats), or
   nothing.
3. **Later — the regulated enterprise.** Departments, SSO/SCIM, audit, and the
   on-prem tier.

Do not build for (3) while (1) has no users.

## Why this exists — the evidence

From ~146 firsthand complaints collected from practitioners (HN, Microsoft Q&A,
Microsoft Tech Community, OpenAI forum, G2, TrustRadius, Capterra, Gartner),
a single documented failure loop repeats:

> A company buys Copilot → it hallucinates on their own internal documents →
> people stop using it → management mandates usage → employees quietly paste
> company data into personal ChatGPT instead → the invoice keeps arriving.

The largest complaint clusters were price-vs-value (19), hallucination on
internal docs (17), adoption failure (16), integration gaps (10), agent noise
(9), model lock-in (9), search failure (9), shadow AI (7), access control (6).
**Essentially all of them are still open** — across every thread examined, not
one contained a product answer the complainant accepted.

Two quotes that should govern design decisions:

- *"How can I trust Copilot if I have to verify everything it answers?"* —
  a user who found 4 of 5 cited URLs fabricated.
- Companies disabled Copilot because it surfaced **salary data** through
  misconfigured SharePoint permissions — it summarized what people technically
  *could*, but shouldn't, access.

## The three product laws

Every feature is judged against these. They are not negotiable.

1. **Every claim carries a source link.** Citation is the trust model, not a
   nice-to-have. An answer without a source is a bug.
2. **Permissions are enforced per-user at retrieval time.** If you cannot see
   the document, the model never saw it. Never inherit an upstream system's
   broken ACLs.
3. **It says "I don't know."** Refusal beats fabrication, always.

## Standing invariants

**ACCOUNT OWNERSHIP.** Every piece of state belongs to exactly one account:
memory, vault, chunks, skills, MCP servers, permissions, usage/spend, portfolio,
missions, notes, todos, files, settings, keys. No global singletons. Any
function that reads or writes state takes an explicit `account_id`. If you
cannot name the owner of a row, the schema is wrong.

**THREE SCOPES.** Knowledge is `personal` | `department` | `company` — the
OneDrive mental model, because every buyer already understands it. Personal
scope is encrypted so that an org admin key cannot decrypt it. The privacy
guarantee comes from **key custody, not physical location** — which is why
personal data lives in the cloud like everything else, and why "your employer
cannot read your personal notes" is a cryptographic claim rather than a
geographic one.

**DENY BY DEFAULT.** Trial expiry, plan limits and the killswitch are a single
entitlement check inside the auth dependency chain, evaluated on every request
that costs money. Order: authenticate → resolve account → check entitlement →
run. Never a cron job that flips a flag; cron fails silently and gives the
product away.

**BUY THE UNDIFFERENTIATED LAYER.** Auth, billing, entitlements, hosting,
compliance automation. Retrieval and the permission model are the product and
are built in-house. Everything else is a purchase.

**MILESTONES, NOT TIMELINES.** Every unit of work has a definition of done that
someone else could verify. No dates.

**DO NOT BREAK WHAT WORKS.** The app has a coherent aesthetic and a real voice
already. Extend; don't replace.

## Decided — do not relitigate

- **Cloud-first.** No machine of ours running 24/7. Desktop becomes a dev
  machine that can be switched off.
- **Stack:** Supabase (Postgres + RLS + pgvector + storage + auth), Railway for
  the FastAPI backend, Vercel for the frontend, Stripe + Autumn for billing and
  entitlements, Sentry. ~$100/month. Chosen for portability, not brand:
  Postgres + a Docker container moves to AWS/Azure in an afternoon if an
  enterprise demands it.
- **We own the `accounts` table.** The auth provider's user id lives in an
  `external_auth_id` column and nowhere else, so the provider stays swappable
  (Supabase Auth now, WorkOS when SSO/SCIM is needed).
- **OpenRouter is the only model path**, with zero-data-retention enforced at
  account and request level. Model neutrality is a required attribute, not the
  pitch — every gateway sells it. Architect provider failover; do not inherit
  one vendor's bad night as our outage.
- **Local/edge models are cut.** They return as an on-prem premium tier sold to
  companies that ban cloud outright — after there is revenue.
- **The Vault is the one knowledge base.** `rag.py` is retired. There is one
  RAG. (Note: the frontend still calls `/rag/*` endpoints that are unmounted —
  those requests 404 today and must be repointed at `/vault/*` or deleted.)
- **Browsing is server-side** (`backend/app/browse.py`): fetch, extract,
  sanitize, render. SSRF-hardened. It ends in the Vault with the source URL as
  the citation. Browsing is a research capability that feeds the knowledge base,
  not a Chrome clone in a tile.
- **Cut and gone:** Spotify (returns later as a connector, not a built-in),
  camera/YOLO/hand-tracking, the 3D RAG vision graph, quote, youtube, sensors,
  animate. ~6,500 lines removed.
- **Kept deliberately:** `ocr.py` — despite sitting in the vision group it is
  *document* OCR, the scanned-PDF fallback for Vault ingestion, with no camera
  dependency. It is an enterprise RAG feature.

## The demo that is the pitch

Build toward this and little else:

> Two accounts, same company, same question — *"what's our comp band for senior
> engineers?"* One answers with citations linked to the source. The other says
> **"I don't have access to that."** Then the admin console shows spend per user
> and a killswitch toggle.

The graph view is the second half of it: a manager sees the company's knowledge
topology coloured by department, switches to "view as: intern," and half the
constellation goes dark. Access control is normally a table of checkboxes — the
least legible way to show the most important thing. Make it visible.

Caveat: graph views go hairball fast and become screensavers. It earns its place
only if it is operational — filter by person or department, click a cluster to
see who can read it, act on it. If it is only beautiful, it is the deleted
Vision tile again.

## The developer surface

The editor is not a second product; it is the wedge that makes developers adopt
this. Its only defensible feature is that **it knows the company's gated
knowledge — Cursor never will.** Prioritise that, don't leave it last.

`CodeTile` is already a small IDE: open a folder → tree, multi-file tabs,
Monaco, dirty state, Ctrl+S. **Do not rebuild it.** Monaco *is* the VS Code
editor core; this is a workbench-and-language-server gap, not an editor gap.

"IDE feel" is capability and keyboard flow, not layout: never type a path
(fuzzy open, go-to-definition, search results as jumps), the editor knows what
the code means (LSP), hands stay on the keyboard, the terminal knows where it
is. Only git and terminal-adjacency need geometry.

**The canvas is an advantage, not an obstacle.** Tabs exist because a window is
small; we have infinite space. Go-to-definition should open the definition
*beside* the caller with both visible — something VS Code structurally cannot
do. Lean into it.

Build order: workspace root concept → real Ctrl+K palette and Ctrl+P fuzzy open
→ cross-file search → LSP → vim mode, git gutter, layout preset. The LSP bridge
should be modelled on `backend/app/mcp_stdio.py`, which is already a working
async stdio JSON-RPC bridge; LSP is the same shape with Content-Length framing.
There are currently zero WebSocket routes in `main.py`.

## Known blockers between here and a hosted beta

In rough dependency order:

1. **DPAPI keystore** — bound to one Windows user on one machine. Cannot run on
   a server. Blocks everything behind it.
2. **`localStorage` as source of truth in ~20 files** — notes, todos, portfolio,
   weather, model choice, permissions, memory and auth context. None of it is
   account-tied. Biggest single blocker to multi-user.
3. **`.data/*.json` + six SQLite files** — all single-user, all need an owner
   column and a home in Postgres.
4. **`main.py` is ~3,800 lines with 52 inline endpoints** and no single place
   that owns "a turn." Extract one `run_turn(account_id, message, ctx)` entry
   point; it is also the one place to inject tenancy later.
5. **Tests are ~516 lines, mostly the stock trader.** Zero coverage on auth,
   retrieval or the vault. For a product whose entire pitch is access control,
   the untested surface *is* the product. The leak test — proving account B
   cannot retrieve account A's document through any query — is the single most
   valuable artifact for an investor conversation.
6. **`allow_origins=["*"]`** — safe on loopback, fatal when hosted.
7. **Updater endpoint is a Tailscale IP over plain HTTP** — works only for us;
   needs a public HTTPS URL before external testers exist.
8. **1.72 MB main JS chunk** (538 KB gzipped) — irrelevant on desktop, a
   multi-second blank screen on the web. Monaco is the obvious lazy chunk.

## Open

- **Name.** "Infinity" is retired — too dreamy; the product should sound stern.
  Candidates: Warden, Attest, Quorum, Bulwark, Keel, Marshal. Needs an available
  `.com`. Do not let this block work; ship under a codename.
- **Trader in a hosted product.** Executing trades for users carries liability a
  personal app does not. Paper/read-only until reviewed.
- **Out-of-app notifications.** The assistant should do work and report back.
  Build the notification layer channel-agnostic; WhatsApp's Business API needs
  Meta approval, a registered number and template review — weeks of paperwork.
  Telegram and email first.

## How to decide when this file is silent

- Does it serve the two-account demo? If not, it is probably later.
- Would it survive being hosted? If it needs our hardware, it is the on-prem
  tier, not the wedge.
- If the two platforms adjacent to this business shook hands tomorrow, would we
  still exist? If no, it is glue — keep moving.
- Are we building breadth before anyone pays? Breadth also corrupts feedback: a
  design partner invents a missing feature rather than telling you it's bad.
