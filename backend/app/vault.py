"""The Vault — a second brain that learns with the user.

Plain markdown is the source of truth. One note = one file with
frontmatter; folders are the domain tree ("Computer Science/Data
Structures/arrays.md"); [[wikilinks]] are the graph. Obsidian can open
the folder directly — no lock-in, no proprietary store. SQLite carries
the *index* (chunks, embeddings, FTS, edges) and is rebuildable from
the files at any time.

Ingestion from any source, one pipeline:

    text/markdown → note                    urls  → readable text → note
    pdf/docx/xlsx/pptx → extracted → note   image → asset + VLM caption note
    audio → local Whisper transcript note   chat/mission reports → notes

Every note is auto-categorized into the domain tree (utility LLM,
validated, biased toward EXISTING domains so the tree converges instead
of sprawling) and auto-linked to its nearest neighbors (local
embeddings). Retrieval is hybrid: vector + FTS keyword + 1-hop graph
expansion, fused by reciprocal rank.

Sovereignty: files local, embeddings pending (M5: Voyage voyage-4, 1024-dim, pgvector),
index local. Only /vault/query's final synthesis touches the chat lane,
and only with the retrieved chunks.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from .paths import data_path

router = APIRouter(prefix="/vault", tags=["vault"])

_REPO_ROOT = Path(__file__).resolve().parents[2]
# The notes and their index MUST live on the same disk.
#
# This used to be `_REPO_ROOT / "vault"`, which is correct on a desktop where the
# repo is permanent, and silently catastrophic in a container: the notes landed
# on /srv (wiped by every redeploy) while _DB_PATH already pointed at the mounted
# volume. After a deploy the directory scanned empty, the index still listed every
# note, and scan() reconciled the difference by DELETING them — including
# chunks.text, which is the last copy of the note bodies. Meanwhile the boot
# banner and the operator console both reported storage as persistent.
#
# data_path() is the single answer to "where does state go"; nothing may compute
# its own. docs/PORTING.md names this; skills_engine.py had the same bug once.
_VAULT_ROOT = Path(os.environ.get("VAULT_DIR") or data_path("vault"))


def vault_dir(account_id: str) -> Path:
    """One account's notes. Notes are FILES, so ownership has to be a directory.

    A shared directory would mean two accounts writing `projects/plan.md` write
    the SAME file — the database predicate would be perfectly correct and the
    data would still be shared, because the rows point at one path on disk.
    That is the failure a column alone cannot prevent.

    Anything already sitting at the old flat root is moved into the single
    tenant's directory on first use. Honest only while there is one tenant,
    which is exactly why it is happening now.
    """
    _VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    _migrate_flat_layout()
    d = _VAULT_ROOT / account_id
    d.mkdir(parents=True, exist_ok=True)
    (d / _ASSETS).mkdir(exist_ok=True)
    return d


_flat_migrated = False


def _migrate_flat_layout() -> None:
    """Move pre-ownership notes from <root>/ into <root>/<single tenant>/."""
    global _flat_migrated
    if _flat_migrated:
        return
    _flat_migrated = True
    from .accounts import SINGLE_TENANT
    target = _VAULT_ROOT / SINGLE_TENANT.id
    strays = [c for c in _VAULT_ROOT.iterdir()
              if c.name != SINGLE_TENANT.id and (c.is_file() or c.name == _ASSETS
                                                 or not _looks_like_account_dir(c))]
    if not strays:
        return
    target.mkdir(parents=True, exist_ok=True)
    import shutil
    moved = 0
    for c in strays:
        dest = target / c.name
        if dest.exists():
            continue                     # already migrated; never overwrite
        shutil.move(str(c), str(dest))
        moved += 1
    if moved:
        print(f"[vault] moved {moved} pre-ownership item(s) into {target.name}/",
              flush=True)


def _looks_like_account_dir(p: Path) -> bool:
    """A uuid-shaped directory name is an account, not a note folder."""
    return p.is_dir() and len(p.name) == 36 and p.name.count("-") == 4
_ASSETS = "_assets"
_DB_PATH = data_path("vault.db")

_CHUNK_CHARS = 1200
_CHUNK_OVERLAP = 150
_MAX_FILE_BYTES = 40 * 1024 * 1024
_LINK_THRESHOLD = 0.55
_LINK_K = 4
_SCAN_EVERY_S = 30.0

_LOCK = threading.Lock()
_conn: sqlite3.Connection | None = None
_fts_ok = True
_last_scan = 0.0
# ONE MATRIX PER ACCOUNT. A single shared cache served whichever account asked
# first to every account that asked later — the SQL was correctly scoped and the
# results still crossed tenants, because the query never ran. Cached data needs
# the same predicate as the query it stands in for.
_vec_cache: dict[str, dict[str, Any]] = {}


def _cache_for(account_id: str) -> dict[str, Any]:
    return _vec_cache.setdefault(account_id, {"dirty": True, "ids": [], "mat": None})

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)(?:[|#][^\]]*)?\]\]")
_DOMAIN_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &+\-']{0,23}$")

# The utility LLM is injected by main (same seam as the mission runtime) so
# this module never imports main.
_utility = None


def init(utility_llm) -> None:
    global _utility
    _utility = utility_llm
    # directories are created lazily by vault_dir(account_id)


# ── clean, readable notes (summarize long/raw extractions) ───────────────────
_SUMMARY_TRIGGER = 2500     # only summarize sources longer than this
_SUMMARY_MAX_SRC = 14000    # chars of source fed to the summarizer (MVP)


async def _summarize_note(title: str, text: str) -> str:
    """Turn a long/raw extraction into a clean English Markdown note.
    Short inputs pass through unchanged. Longer ones are summarized on the
    UTILITY lane, then the DISPATCH (OpenRouter) lane as a fallback — so
    summarize-on-ingest works whenever the app has EITHER key, not just the
    utility one. Never raises: if no summarizer is reachable it still returns
    CLEAN markdown (a header + the raw extraction folded into a source block),
    NEVER a raw truncated dump as the note body (fixes the 'garbage PDF node')."""
    clean = (text or "").strip()
    if not clean:
        return f"_(empty document: {title})_"
    if len(clean) <= _SUMMARY_TRIGGER:
        return clean

    summary = await _try_summarize(title, clean)
    if summary:
        return summary

    # No summarizer available/failed -> still produce readable markdown. The
    # summary can't be written, so lead with a one-liner and fold the raw
    # extraction into a fenced source block (so the reader shows structure, not
    # a wall of mixed-language raw text).
    excerpt = clean[:6000] + ("\n\n…(truncated)" if len(clean) > 6000 else "")
    return (f"**{title}** — imported document (no summarizer available; showing "
            f"the extracted source).\n\n## Source (raw extraction)\n\n```\n{excerpt}\n```")


async def _try_summarize(title: str, clean: str) -> str:
    """Summarize on the utility lane, then the dispatch lane. Returns "" if
    neither is reachable or both fail. Never raises."""
    prompt = (
        f"Summarize the document titled '{title}' into a clean, well-structured "
        "Markdown note in ENGLISH. If the document repeats itself in other "
        "languages (e.g. Chinese, Portuguese translations of the same content), "
        "use ONLY the English content. Produce exactly:\n"
        "- one sentence saying what it is\n"
        "- a '## Key points' bullet list of the facts/specs that matter\n"
        "- a '## Details' section ONLY for genuinely useful specifics\n"
        "Be faithful — no invention. Under 400 words. No preamble, no code fence.\n\n"
        f"DOCUMENT:\n{clean[:_SUMMARY_MAX_SRC]}"
    )
    # 1) injected utility lane (cheap; may be local CPU)
    if _utility is not None:
        try:
            out = await _utility(prompt, temperature=0.1, max_tokens=900)
            if out and out.strip():
                return out.strip()
        except Exception as e:
            print(f"[vault] utility summarize failed (non-fatal): {e}", flush=True)
    # 2) dispatch lane (OpenRouter) — works whenever a dispatch key is present
    try:
        from .providers import dispatch
        provider = dispatch.get_provider()
        if provider is not None:
            model = dispatch.resolve_model("smart")
            out = await provider.complete([{"role": "user", "content": prompt}],
                                          model=model, temperature=0.1, max_tokens=900)
            if out and out.strip():
                return out.strip()
    except Exception as e:
        print(f"[vault] dispatch summarize failed (non-fatal): {e}", flush=True)
    return ""


# ── storage ──────────────────────────────────────────────────────────────────

def _migrate_ownership(db: sqlite3.Connection) -> None:
    """Give every vault row an owner. Idempotent; safe to run on every boot.

    THE HARD PART IS `nodes`. Its `slug` and `rel_path` were declared UNIQUE
    globally, and SQLite cannot drop a constraint — so those have to become
    UNIQUE(owner_account_id, ...) via a table rebuild rather than an ALTER.
    A global slug would mean two accounts could never own a note called the
    same thing, and the second one to try would get a constraint error from
    somebody else's data.

    SLUGS ARE ACCOUNT-LOCAL, not org-shared. DIRECTION.md's answer to "who can
    see this" is scope, and the owner's instruction was that knowledge is
    personal for now. It is also the reversible direction: an org-shared alias
    layer can be added on top later, whereas narrowing a shared namespace would
    break every existing [[wikilink]].

    `chunks` and `edges` carry the owner DENORMALISED rather than joining to
    `nodes`. _vec_matrix() selects from `chunks` alone and must filter without a
    join, and an owner predicate that depends on a join is one refactor away
    from being dropped.
    """
    from .accounts import SINGLE_TENANT
    owner, org = SINGLE_TENANT.id, SINGLE_TENANT.org_id

    have = {r[1] for r in db.execute("PRAGMA table_info(nodes)").fetchall()}
    if "owner_account_id" not in have:
        db.executescript(
            """
            CREATE TABLE nodes_owned (
                id TEXT PRIMARY KEY,
                owner_account_id TEXT NOT NULL,
                org_id TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT 'personal',
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                domain TEXT NOT NULL, tags TEXT NOT NULL,
                source_kind TEXT NOT NULL DEFAULT 'note',
                source_ref TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL,
                mtime REAL NOT NULL, chars INTEGER NOT NULL,
                UNIQUE(owner_account_id, slug),
                UNIQUE(owner_account_id, rel_path)
            );
            """
        )
        db.execute(
            "INSERT INTO nodes_owned (id, owner_account_id, org_id, slug, title,"
            " rel_path, domain, tags, source_kind, source_ref, created, updated,"
            " mtime, chars)"
            " SELECT id, ?, ?, slug, title, rel_path, domain, tags, source_kind,"
            "        source_ref, created, updated, mtime, chars FROM nodes",
            (owner, org),
        )
        db.execute("DROP TABLE nodes")
        db.execute("ALTER TABLE nodes_owned RENAME TO nodes")
    db.execute("CREATE INDEX IF NOT EXISTS idx_nodes_owner ON nodes(owner_account_id)")

    # chunks / edges: a plain column each, backfilled from the node they belong to.
    for table, via in (("chunks", "node_id"), ("edges", "src")):
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if "owner_account_id" not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN owner_account_id TEXT")
            db.execute(
                f"UPDATE {table} SET owner_account_id ="
                f" COALESCE((SELECT n.owner_account_id FROM nodes n WHERE n.id = {table}.{via}), ?)",
                (owner,),
            )
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_owner"
                   f" ON {table}(owner_account_id)")
    db.commit()


def _db() -> sqlite3.Connection:
    global _conn, _fts_ok
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL, rel_path TEXT UNIQUE NOT NULL,
                domain TEXT NOT NULL, tags TEXT NOT NULL,
                source_kind TEXT NOT NULL DEFAULT 'note',
                source_ref TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL,
                mtime REAL NOT NULL, chars INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL, ord INTEGER NOT NULL,
                text TEXT NOT NULL, vec BLOB
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_node ON chunks(node_id);
            CREATE TABLE IF NOT EXISTS edges (
                src TEXT NOT NULL, dst TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'link',
                PRIMARY KEY (src, dst, kind)
            );
            """
        )
        _migrate_ownership(_conn)

        # A VECTOR WITHOUT ITS PROVENANCE IS NOT COMPARABLE TO ANYTHING.
        #
        # `vec` used to be a bare BLOB. Two models produce vectors that are not
        # comparable even at the same dimension, and the numbers still multiply
        # and still yield a ranking — so an index holding both looks healthy and
        # returns noise. At different dimensions it is worse in a different way:
        # _vec_matrix() concatenates every row and reshapes, so one stale
        # 768-dim row would break or silently mis-shape the whole matrix.
        #
        # Recording which model and dimension produced each row makes both
        # cases detectable, and lets readers skip rows they cannot compare
        # instead of comparing them anyway.
        # SQLite has no ADD COLUMN IF NOT EXISTS. Ask what is there rather than
        # attempting the ALTER and swallowing the failure — a blanket except
        # here would also hide a genuinely broken database.
        have = {r[1] for r in _conn.execute("PRAGMA table_info(chunks)").fetchall()}
        for col, decl in (("model", "TEXT"), ("dim", "INTEGER")):
            if col not in have:
                _conn.execute(f"ALTER TABLE chunks ADD COLUMN {col} {decl}")
        # FTS carries the owner too, or keyword search answers what vector
        # search refuses. It is a VIRTUAL table so it cannot be ALTERed — but it
        # is also pure derived data, rebuildable from chunks.text, so dropping
        # and repopulating costs nothing but time.
        try:
            cur = _conn.execute("PRAGMA table_info(fts)").fetchall()
            if cur and not any(r[1] == "owner_account_id" for r in cur):
                _conn.execute("DROP TABLE fts")
            _conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5"
                "(text, node_id UNINDEXED, chunk_id UNINDEXED, owner_account_id UNINDEXED)"
            )
            # Dropping it above leaves it empty. Repopulate from chunks, which
            # is the source of truth — otherwise keyword search silently returns
            # nothing and looks exactly like an empty vault.
            n_fts = _conn.execute("SELECT COUNT(*) FROM fts").fetchone()[0]
            n_chunks = _conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if n_chunks and not n_fts:
                _conn.execute(
                    "INSERT INTO fts (text, node_id, chunk_id, owner_account_id)"
                    " SELECT text, node_id, id, owner_account_id FROM chunks"
                )
                print(f"[vault] rebuilt the keyword index for {n_chunks} chunk(s)",
                      flush=True)
        except sqlite3.OperationalError:
            _fts_ok = False  # keyword search degrades to LIKE
        _conn.commit()
    return _conn


def _account_id() -> str:
    """Whose vault this request touches.

    One tenant today, and the app-level auth dependency has already verified the
    session before any route runs. A function rather than a constant so that
    when identity lands this becomes a request dependency and every call site is
    already asking the right question.
    """
    from .accounts import SINGLE_TENANT
    return SINGLE_TENANT.id


def _account_id_dep() -> str:
    """FastAPI-dependency form of _account_id().

    A dependency rather than a keyword-only argument because FastAPI would read
    a bare `account_id: str` off the QUERY STRING — letting the caller name the
    account whose vault they wanted to read, which is the opposite of the point.
    """
    return _account_id()


def _mark_dirty() -> None:
    for c in _vec_cache.values():
        c["dirty"] = True


# ── markdown node format ─────────────────────────────────────────────────────

def _slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:60] or "note"


def _parse_note(text: str) -> tuple[dict[str, str], str]:
    """(frontmatter dict, body). Tolerant: no frontmatter → empty meta."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, text[m.end():].strip()


def _render_note(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for k in ("id", "title", "domain", "tags", "source", "created", "updated"):
        if meta.get(k):
            lines.append(f"{k}: {meta[k]}")
    lines.append("---\n")
    return "\n".join(lines) + body.strip() + "\n"


def _chunk_text(text: str) -> list[str]:
    """Line-aware overlapping chunks (same approach the RAG lane proved)."""
    text = (text or "").strip()
    if not text:
        return []
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in text.split("\n"):
        if cur_len + len(line) > _CHUNK_CHARS and cur:
            block = "\n".join(cur).strip()
            if block:
                out.append(block)
            keep: list[str] = []
            klen = 0
            for prev in reversed(cur):
                if klen + len(prev) > _CHUNK_OVERLAP:
                    break
                keep.insert(0, prev)
                klen += len(prev)
            cur, cur_len = keep, klen
        cur.append(line)
        cur_len += len(line) + 1
        while cur_len > 3 * _CHUNK_CHARS:  # single pathological line
            block = "\n".join(cur)
            out.append(block[:_CHUNK_CHARS])
            rest = block[_CHUNK_CHARS - _CHUNK_OVERLAP:]
            cur, cur_len = [rest], len(rest)
    block = "\n".join(cur).strip()
    if block:
        out.append(block)
    return out


# Last embedding failure, and when it was last logged. NOT a one-shot flag:
# the first version logged once per process, which meant a transient rate limit
# was reported once and then failed silently forever after — the exact
# "let a failure be silent" trap this codebase keeps falling into.
_embed_fail: dict[str, Any] = {"reason": None, "at": 0.0, "logged": 0.0}
_EMBED_LOG_EVERY_S = 60.0


def last_embed_failure() -> str | None:
    """Why the most recent embed failed, if it was recent enough to matter."""
    if not _embed_fail["reason"]:
        return None
    if time.time() - float(_embed_fail["at"]) > 300:
        return None
    return str(_embed_fail["reason"])


def _note_embed_failure(e: Exception) -> None:
    """Record it, and log at most once a minute.

    Rate-limited rather than one-shot: a per-minute provider cap produces a
    steady stream of identical failures, and both extremes are wrong — logging
    every one floods the log, logging only the first makes an ongoing outage
    invisible after the first line.
    """
    now = time.time()
    _embed_fail["reason"] = str(e)
    _embed_fail["at"] = now
    if now - float(_embed_fail["logged"]) >= _EMBED_LOG_EVERY_S:
        _embed_fail["logged"] = now
        print(f"[vault] embedding failed — affected chunks are stored WITHOUT "
              f"vectors and search falls back to full text. "
              f"POST /vault/reindex to fill them in. ({e})", flush=True)


async def _embed(text: str, *, input_type: str = "document") -> bytes | None:
    """Vector bytes, or None when embeddings are unavailable.

    None is deliberate and is NOT the same as a zero vector: the chunk is still
    stored and still reachable by full-text search, it simply has no vector
    until /vault/reindex fills one in. Swallowing the error keeps ingestion
    working on a deployment with no embedding provider — but it used to swallow
    it SILENTLY, which is how the index filled with unembedded chunks and
    nothing said so. It now says so once.

    `input_type` matters: Voyage embeds the same sentence differently as a
    stored document than as a question about one, so searching must pass
    "query" or every result is slightly wrong in a way nothing surfaces.
    """
    try:
        from reactor.memory.embed import embed_text
        v = await embed_text(text, input_type=input_type)
        n = float(np.linalg.norm(v))
        if n <= 0:
            return None
        _embed_fail["reason"] = None
        return (v / n).astype(np.float32).tobytes()
    except Exception as e:
        _note_embed_failure(e)
        return None


async def _embed_many(texts: list[str]) -> list[bytes | None]:
    """Vector bytes for a whole note, in ONE request. None for every chunk if
    embeddings are unavailable.

    Indexing used to call _embed() per chunk, so writing one note meant a
    separate HTTPS round trip (and TLS handshake) per chunk, serially — paid
    again for every changed file on every scan(). The provider takes batches;
    this is the difference between one request and dozens.

    Empty chunks are held out rather than sent: the provider refuses an empty
    string, and one blank chunk must not cost the note its entire index.
    """
    if not texts:
        return []
    idx = [i for i, t in enumerate(texts) if (t or "").strip()]
    if not idx:
        return [None] * len(texts)

    from .providers.embeddings import EmbedRefused, embed_texts
    try:
        vecs = await embed_texts([texts[i].strip() for i in idx], input_type="document")
    except EmbedRefused:
        # Deliberate, not broken. Propagate so the caller can decline to destroy
        # anything — see _index_file. Swallowing it here is what turned a read
        # during a pause into index corruption.
        raise
    except Exception as e:
        _note_embed_failure(e)
        return [None] * len(texts)

    out: list[bytes | None] = [None] * len(texts)
    for i, v in zip(idx, vecs):
        out[i] = v.astype(np.float32).tobytes()
    return out


# ── indexing (files → SQLite) ────────────────────────────────────────────────

def _domain_from_rel(rel: Path) -> list[str]:
    parts = [p for p in rel.parts[:-1] if p and p != _ASSETS]
    return list(parts[:3]) or ["Inbox"]


async def _index_file(abs_path: Path, *, account_id: str) -> str | None:
    """(Re)index one note file. Returns node id. Folders are authoritative
    for domain (Obsidian moves must win); frontmatter carries identity."""
    root = vault_dir(account_id)
    rel = abs_path.relative_to(root)
    try:
        raw = abs_path.read_text("utf-8", errors="replace")
    except Exception:
        return None
    meta, body = _parse_note(raw)
    title = meta.get("title") or ""
    if not title:
        h = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = (h.group(1).strip() if h else abs_path.stem.replace("-", " "))[:120]
    # `id` is the global PRIMARY KEY, so it must include the owner. Derived from
    # the path alone, two accounts with the same relative path produced the SAME
    # id and the second one's note was rejected with a constraint error against a
    # row they cannot see. Deterministic (not random) so re-indexing a file keeps
    # its identity, which is what makes the upsert an upsert.
    node_id = meta.get("id") or f"nd_{abs(hash((account_id, str(rel)))) % 10**10:010d}"
    slug = _slugify(meta.get("title") or abs_path.stem)
    domain = _domain_from_rel(rel)
    tags = [t.strip() for t in (meta.get("tags") or "").split(",") if t.strip()]
    src = meta.get("source") or "note"
    kind, _, ref = src.partition(":")
    mtime = abs_path.stat().st_mtime
    created = time.time()
    try:
        from datetime import datetime
        if meta.get("created"):
            created = datetime.fromisoformat(
                meta["created"].replace("Z", "+00:00")).timestamp()
    except Exception:
        pass

    chunks = _chunk_text(body)
    from .providers.embeddings import EmbedRefused
    try:
        vecs = await _embed_many(chunks)
    except EmbedRefused:
        # DO NOT DESTROY A WORKING INDEX TO REBUILD IT WITH NOTHING.
        #
        # Below, this note's chunks are deleted and re-inserted. Fine when the
        # embed merely failed — the rows return without vectors and a backfill
        # fixes them. NOT fine when embedding was refused on purpose: /vault/tree,
        # /vault/graph and /vault/stats all call scan() first, so simply READING
        # the vault while spending is paused would replace every edited note's
        # working vectors with nulls.
        with _LOCK:
            already = _db().execute(
                "SELECT COUNT(*) FROM chunks WHERE node_id=? AND owner_account_id=?",
                (node_id, account_id)).fetchone()[0]
        if already:
            print(f"[vault] not re-indexing {rel} while embedding is paused — "
                  f"its existing vectors are left intact", flush=True)
            return node_id
        # A note with nothing to protect is still worth indexing; it just gets
        # no vectors, and /vault/reindex fills them in when spending resumes.
        vecs = [None] * len(chunks)

    links = {_slugify(m) for m in _WIKILINK_RE.findall(body)}

    with _LOCK:
        db = _db()
        db.execute("DELETE FROM chunks WHERE node_id=? AND owner_account_id=?", (node_id, account_id))
        if _fts_ok:
            db.execute("DELETE FROM fts WHERE node_id=? AND owner_account_id=?", (node_id, account_id))
        db.execute("DELETE FROM edges WHERE src=? AND owner_account_id=?", (node_id, account_id))
        db.execute(
            """INSERT INTO nodes (id, owner_account_id, org_id, slug, title,
                                  rel_path, domain, tags,
                                  source_kind, source_ref, created, updated,
                                  mtime, chars)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(owner_account_id, rel_path) DO UPDATE SET
                   id=excluded.id, slug=excluded.slug, title=excluded.title,
                   domain=excluded.domain, tags=excluded.tags,
                   source_kind=excluded.source_kind, source_ref=excluded.source_ref,
                   updated=excluded.updated, mtime=excluded.mtime,
                   chars=excluded.chars""",
            (node_id, account_id, "", slug, title, str(rel).replace("\\", "/"),
             json.dumps(domain), json.dumps(tags), kind or "note", ref,
             created, time.time(), mtime, len(body)),
        )
        # Stamp which model/dimension produced each vector, so a later change
        # of provider is detectable instead of silently poisoning the index.
        from .providers.embeddings import active_dim, active_model
        emodel, edim = active_model(), active_dim()
        for i, (ch, vb) in enumerate(zip(chunks, vecs)):
            cur = db.execute(
                "INSERT INTO chunks (node_id, ord, text, vec, model, dim,"
                " owner_account_id) VALUES (?,?,?,?,?,?,?)",
                (node_id, i, ch, vb,
                 emodel if vb is not None else None,
                 edim if vb is not None else None,
                 account_id),
            )
            if _fts_ok:
                db.execute(
                    "INSERT INTO fts (text, node_id, chunk_id, owner_account_id)"
                    " VALUES (?,?,?,?)",
                    (ch, node_id, cur.lastrowid, account_id))
        for target_slug in links:
            row = db.execute(
                "SELECT id FROM nodes WHERE owner_account_id=? AND slug=?",
                (account_id, target_slug)).fetchone()
            if row:
                db.execute(
                    "INSERT OR IGNORE INTO edges (src,dst,kind,owner_account_id)"
                    " VALUES (?,?,'link',?)",
                    (node_id, row[0], account_id),
                )
        db.commit()
    _mark_dirty()
    return node_id


async def scan(force: bool = False, *, account_id: str) -> dict[str, int]:
    """Reconcile vault files ↔ index by mtime. Cheap when nothing changed."""
    global _last_scan
    now = time.monotonic()
    if not force and now - _last_scan < _SCAN_EVERY_S:
        return {"scanned": 0, "indexed": 0, "removed": 0}
    _last_scan = now
    root = vault_dir(account_id)
    seen: set[str] = set()
    indexed = 0
    files = [p for p in root.rglob("*.md")
             if _ASSETS not in p.relative_to(root).parts]
    with _LOCK:
        known = {r[0]: r[1] for r in
                 _db().execute("SELECT rel_path, mtime FROM nodes WHERE owner_account_id=?",
                            (account_id,)).fetchall()}
    for p in files:
        rel = str(p.relative_to(root)).replace("\\", "/")
        seen.add(rel)
        try:
            if abs(known.get(rel, -1) - p.stat().st_mtime) > 1e-6:
                if await _index_file(p, account_id=account_id) is not None:
                    indexed += 1
        except Exception as e:
            print(f"[vault] index {rel}: {type(e).__name__}: {e}", flush=True)
    # REFUSE TO MASS-DELETE. Reconciliation assumes the directory is the truth,
    # which holds only when the directory is really there. If VAULT_DIR points
    # somewhere empty — an unmounted volume, a wrong path after a deploy, a
    # renamed directory — then "every note was deleted" and "the disk is missing"
    # look identical from here, and one of those readings destroys the corpus.
    #
    # So when the scan finds NOTHING but the index holds notes, treat it as a
    # missing disk rather than a mass deletion. A user really emptying their
    # vault loses nothing: the rows persist until a scan that can actually see
    # the directory, and re-creating one file clears the condition.
    missing = set(known) - seen
    if missing and not files and known:
        print(f"[vault] REFUSING to delete {len(missing)} indexed notes: "
              f"{root} scanned empty. This is what a missing or unmounted "
              f"volume looks like, not an empty vault. Index left intact.",
              flush=True)
        return {"scanned": 0, "indexed": indexed, "removed": 0, "protected": len(missing)}

    removed = 0
    with _LOCK:
        db = _db()
        for rel in missing:
            row = db.execute("SELECT id FROM nodes WHERE owner_account_id=? AND rel_path=?",
                             (account_id, rel)).fetchone()
            if row:
                nid = row[0]
                db.execute("DELETE FROM chunks WHERE node_id=? AND owner_account_id=?", (nid, account_id))
                if _fts_ok:
                    db.execute("DELETE FROM fts WHERE node_id=? AND owner_account_id=?", (nid, account_id))
                db.execute("DELETE FROM edges WHERE owner_account_id=? AND (src=? OR dst=?)", (account_id, nid, nid))
                db.execute("DELETE FROM nodes WHERE id=? AND owner_account_id=?", (nid, account_id))
                removed += 1
        db.commit()
    if removed:
        _mark_dirty()
    return {"scanned": len(files), "indexed": indexed, "removed": removed}


# ── categorization + creation ────────────────────────────────────────────────

def _domain_tree(max_depth: int = 2, *, account_id: str) -> list[str]:
    with _LOCK:
        rows = _db().execute("SELECT domain FROM nodes WHERE owner_account_id=?",
                             (account_id,)).fetchall()
    seen: dict[str, None] = {}
    for (dj,) in rows:
        try:
            parts = json.loads(dj)[:max_depth]
        except Exception:
            continue
        for i in range(len(parts)):
            seen[" / ".join(parts[: i + 1])] = None
    return sorted(seen)


async def classify_domain(title: str, text: str, *, account_id: str,
                          hint: list[str] | None = None) -> list[str]:
    """Utility-LLM domain path, validated hard, biased to the existing tree.
    Fallback: ['Inbox'] — never blocks an ingest."""
    if hint:
        clean = [h for h in hint[:3] if _DOMAIN_PART_RE.match(h or "")]
        if clean:
            return clean
    if _utility is None:
        return ["Inbox"]
    tree = _domain_tree(account_id=account_id)
    tree_txt = "\n".join(f"- {t}" for t in tree[:80]) or "- (empty)"
    raw = await _utility(
        "Categorize this note into a knowledge-tree path, 1-3 levels, "
        "general→specific (e.g. [\"Computer Science\",\"Data Structures\","
        "\"Arrays\"]). STRONGLY prefer extending an EXISTING branch:\n"
        f"{tree_txt}\n\nTITLE: {title}\nNOTE (start):\n{text[:1500]}\n\n"
        'Respond ONLY with a JSON array of 1-3 TitleCase strings, each <=24 chars.',
        temperature=0.0, max_tokens=80,
    )
    try:
        s = (raw or "").strip()
        arr = json.loads(s[s.find("["): s.rfind("]") + 1])
        parts = [str(p).strip() for p in arr][:3]
        parts = [p for p in parts if _DOMAIN_PART_RE.match(p)]
        if parts:
            return parts
    except Exception:
        pass
    return ["Inbox"]


async def _related_slugs(text: str, exclude_node: str | None, k: int = _LINK_K, *, account_id: str) -> list[str]:
    hits = await _vector_search(text[:2000], k * 3, account_id=account_id)
    out: list[str] = []
    seen: set[str] = set()
    for h in hits:
        if h["score"] < _LINK_THRESHOLD or h["node_id"] == exclude_node:
            continue
        if h["slug"] in seen:
            continue
        seen.add(h["slug"])
        out.append(h["slug"])
        if len(out) >= k:
            break
    return out


async def write_node(*, account_id: str, title: str, body: str, domain: list[str] | None = None,
                     tags: list[str] | None = None, source_kind: str = "note",
                     source_ref: str = "") -> dict[str, Any]:
    """Create a note: classify → place in the tree → auto-link → write file
    → index. The learn-with-me entry point everything else calls."""
    title = (title or "Untitled").strip()[:120]
    body = (body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="empty note body")
    dom = domain or await classify_domain(title, body, account_id=account_id)
    related = await _related_slugs(body, exclude_node=None, account_id=account_id)
    if related:
        body += "\n\n## Related\n" + "\n".join(f"- [[{s}]]" for s in related)

    folder = vault_dir(account_id).joinpath(*dom)
    folder.mkdir(parents=True, exist_ok=True)
    slug = base = _slugify(title)
    i = 2
    while (folder / f"{slug}.md").exists() or _slug_taken(slug, account_id=account_id):
        slug = f"{base}-{i}"
        i += 1
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "id": f"nd_{int(time.time()*1000) % 10**10:010d}",
        "title": title,
        "domain": " / ".join(dom),
        "tags": ", ".join((tags or [])[:8]),
        "source": f"{source_kind}:{source_ref}" if source_ref else source_kind,
        "created": now_iso,
    }
    path = folder / f"{slug}.md"
    path.write_text(_render_note(meta, body), "utf-8")
    node_id = await _index_file(path, account_id=account_id)
    return {"id": node_id, "slug": slug, "title": title, "domain": dom,
            "rel_path": str(path.relative_to(vault_dir(account_id))).replace("\\", "/"),
            "linked": related}


def _slug_taken(slug: str, *, account_id: str) -> bool:
    with _LOCK:
        return _db().execute(
            "SELECT 1 FROM nodes WHERE owner_account_id=? AND slug=?",
            (account_id, slug)).fetchone() is not None


async def file_report(title: str, text: str, *, account_id: str, source_kind: str,
                      source_ref: str = "") -> dict[str, Any] | None:
    """Reports the OS generates land back as notes (the learn-with-me loop).
    Soft-fails — a vault hiccup must never break the producing feature."""
    try:
        return await write_node(account_id=account_id, title=title, body=text,
                                source_kind=source_kind, source_ref=source_ref)
    except Exception as e:
        print(f"[vault] file_report failed (non-fatal): {e}", flush=True)
        return None


# ── retrieval ────────────────────────────────────────────────────────────────

def _vec_matrix(*, account_id: str) -> tuple[list[tuple[int, str]], Any]:
    """(chunk meta, normalized matrix) — cached until the index changes.

    ONLY rows produced by the ACTIVE model at the ACTIVE dimension are loaded.
    Anything else is skipped rather than compared: a vector from another model
    still multiplies cleanly against the query and still produces a confident
    ranking, so mixing them does not fail, it just returns noise. Rows written
    before provenance existed have model NULL and are treated as unusable —
    /vault/reindex re-embeds them.
    """
    cache = _cache_for(account_id)
    if not cache["dirty"] and cache["mat"] is not None:
        return cache["ids"], cache["mat"]

    from .providers.embeddings import active_dim, active_model
    model, dim = active_model(), active_dim()
    if not model or not dim:
        cache.update(ids=[], mat=None, dirty=False)
        return [], None

    with _LOCK:
        rows = _db().execute(
            "SELECT id, node_id, vec FROM chunks "
            "WHERE owner_account_id = ? AND vec IS NOT NULL AND model = ? AND dim = ?",
            (account_id, model, dim),
        ).fetchall()
    # Belt and braces: a row whose blob length disagrees with its own recorded
    # dim would still break the reshape below, and one bad row must not take
    # out the whole index.
    want = dim * 4  # float32
    good = [r for r in rows if len(r[2]) == want]
    if len(good) != len(rows):
        print(f"[vault] skipped {len(rows) - len(good)} chunk vector(s) whose "
              f"size disagrees with their recorded dimension", flush=True)
    ids = [(r[0], r[1]) for r in good]
    mat = (np.frombuffer(b"".join(r[2] for r in good), dtype=np.float32)
           .reshape(len(good), dim) if good else None)
    cache.update(ids=ids, mat=mat, dirty=False)
    return ids, mat


def _node_meta(node_ids: list[str], *, account_id: str) -> dict[str, dict]:
    if not node_ids:
        return {}
    q = ",".join("?" * len(node_ids))
    with _LOCK:
        rows = _db().execute(
            f"SELECT id, slug, title, domain FROM nodes WHERE owner_account_id=? AND id IN ({q})",
            [account_id, *node_ids],
        ).fetchall()
    return {r[0]: {"slug": r[1], "title": r[2], "domain": json.loads(r[3])}
            for r in rows}


async def _vector_search(query: str, k: int, *, account_id: str) -> list[dict]:
    # "query", not "document": the same sentence embeds differently as a
    # question than as stored text, and getting this wrong degrades every
    # result without failing anything.
    vb = await _embed(query, input_type="query")
    if vb is None:
        return []
    ids, mat = _vec_matrix(account_id=account_id)
    if mat is None or not len(ids):
        return []
    qv = np.frombuffer(vb, dtype=np.float32)
    sims = mat @ qv
    order = np.argsort(-sims)[:k]
    metas = _node_meta(list({ids[i][1] for i in order}), account_id=account_id)
    out = []
    with _LOCK:
        for i in order:
            cid, nid = ids[i]
            row = _db().execute(
                "SELECT text FROM chunks WHERE id=? AND owner_account_id=?",
                (cid, account_id)).fetchone()
            m = metas.get(nid, {})
            out.append({"chunk_id": cid, "node_id": nid, "score": float(sims[i]),
                        "text": row[0] if row else "", **m})
    return out


def _keyword_search(query: str, k: int, *, account_id: str) -> list[dict]:
    with _LOCK:
        db = _db()
        rows: list = []
        if _fts_ok:
            try:
                safe = " ".join(re.findall(r"[\w']+", query)) or query
                rows = db.execute(
                    "SELECT chunk_id, node_id, text, bm25(fts) FROM fts "
                    "WHERE owner_account_id = ? AND fts MATCH ? "
                    "ORDER BY bm25(fts) LIMIT ?",
                    (account_id, safe, k),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            like = f"%{query[:60]}%"
            rows = [(r[0], r[1], r[2], 0.0) for r in db.execute(
                "SELECT id, node_id, text, 0 FROM chunks"
                " WHERE owner_account_id = ? AND text LIKE ? LIMIT ?",
                (account_id, like, k),
            ).fetchall()]
    metas = _node_meta(list({r[1] for r in rows}), account_id=account_id)
    return [{"chunk_id": r[0], "node_id": r[1], "text": r[2],
             "score": 0.0, **metas.get(r[1], {})} for r in rows]


async def retrieve(question: str, k: int = 8, *, account_id: str) -> list[dict]:
    """Hybrid retrieval: vector + keyword fused by reciprocal rank, then
    1-hop graph expansion from the top nodes."""
    vec = await _vector_search(question, k * 2, account_id=account_id)
    kw = _keyword_search(question, k, account_id=account_id)
    fused: dict[int, dict] = {}
    for rank, h in enumerate(vec):
        f = fused.setdefault(h["chunk_id"], {**h, "rrf": 0.0})
        f["rrf"] += 1.0 / (10 + rank)
    for rank, h in enumerate(kw):
        f = fused.setdefault(h["chunk_id"], {**h, "rrf": 0.0})
        f["rrf"] += 0.8 / (10 + rank)
    ranked = sorted(fused.values(), key=lambda h: -h["rrf"])[:k]

    # 1-hop expansion: linked neighbors of the top nodes, lightly weighted.
    top_nodes = list({h["node_id"] for h in ranked[:3]})
    if top_nodes:
        q = ",".join("?" * len(top_nodes))
        with _LOCK:
            nbrs = [r[0] for r in _db().execute(
                f"SELECT DISTINCT dst FROM edges WHERE owner_account_id=? AND src IN ({q}) "
                f"UNION SELECT DISTINCT src FROM edges WHERE owner_account_id=? AND dst IN ({q})",
                [account_id, *top_nodes, account_id, *top_nodes],
            ).fetchall() if r[0] not in top_nodes][:4]
        if nbrs:
            metas = _node_meta(nbrs, account_id=account_id)
            with _LOCK:
                for nid in nbrs:
                    row = _db().execute(
                        "SELECT id, text FROM chunks WHERE node_id=? AND owner_account_id=?"
                        " ORDER BY ord LIMIT 1",
                        (nid, account_id),
                    ).fetchone()
                    if row and all(h["chunk_id"] != row[0] for h in ranked):
                        ranked.append({"chunk_id": row[0], "node_id": nid,
                                       "text": row[1], "score": 0.0,
                                       "rrf": 0.01, **metas.get(nid, {})})
    return ranked[:k + 3]


# ── endpoints ────────────────────────────────────────────────────────────────

class IngestIn(BaseModel):
    text: str | None = None
    url: str | None = None
    title: str | None = None
    domain: list[str] | None = None
    tags: list[str] | None = None


@router.post("/ingest")
async def vault_ingest(body: IngestIn, account_id: str = Depends(_account_id_dep)) -> dict:
    await scan(account_id=account_id)
    if body.url:
        # Routed through app.browse: SSRF-hardened fetch + real extraction.
        # (Replaces a raw httpx.get + regex title + the retired rag helper.)
        from .browse import _fetch  # noqa: PLC0415 — lazy, avoids an import cycle
        page = await _fetch(body.url)
        text = page.text
        if not text.strip():
            raise HTTPException(status_code=422, detail="no readable text at URL")
        title = (body.title or page.title or body.url).strip()
        summary = await _summarize_note(title[:120], text)
        node = await write_node(account_id=account_id, title=title[:120], body=summary,
                                domain=body.domain, tags=body.tags,
                                source_kind="url", source_ref=body.url[:300])
        return {"ok": True, "node": node}
    if body.text and body.text.strip():
        title = body.title or body.text.strip().splitlines()[0][:80]
        node = await write_node(account_id=account_id, title=title, body=body.text.strip(),
                                domain=body.domain, tags=body.tags,
                                source_kind="text")
        return {"ok": True, "node": node}
    raise HTTPException(status_code=422, detail="provide text or url")


_DOC_EXTS = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".pptx"}
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_AUD_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"}


@router.post("/ingest/file")
async def vault_ingest_file(file: UploadFile = File(...),
                            account_id: str = Depends(_account_id_dep)) -> dict:
    await scan(account_id=account_id)
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=422, detail="empty file")
    if len(blob) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    name = file.filename or "upload"
    ext = Path(name).suffix.lower()
    stem = Path(name).stem[:80]

    if ext in _DOC_EXTS:
        if ext in (".md", ".txt"):
            text = blob.decode("utf-8", errors="replace")
        else:
            from . import files as files_mod
            extractor = {".pdf": files_mod._extract_pdf,
                         ".docx": files_mod._extract_docx,
                         ".xlsx": files_mod._extract_xlsx,
                         ".pptx": files_mod._extract_pptx}[ext]
            try:
                text, _meta = extractor(blob)
            except Exception:
                raise HTTPException(status_code=422,
                                    detail=f"could not extract text from {ext}")
        if not (text or "").strip():
            # Scanned / image-only PDF ("No readable text") → OCR via the vision lane.
            if ext == ".pdf":
                try:
                    from . import ocr as ocr_mod
                    text = await ocr_mod.ocr_pdf(blob)
                except Exception as e:
                    print(f"[vault] ocr fallback failed: {e}", flush=True)
            if not (text or "").strip():
                raise HTTPException(status_code=422,
                                    detail="no text in document (extraction + OCR found nothing)")
        body = await _summarize_note(stem, text)
        node = await write_node(account_id=account_id, title=stem, body=body,
                                source_kind="file", source_ref=name[:200])
        return {"ok": True, "node": node, "kind": "document"}

    if ext in _IMG_EXTS:
        asset_dir = vault_dir(account_id) / _ASSETS
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset = asset_dir / f"{_slugify(stem)}-{int(time.time())}{ext}"
        asset.write_bytes(blob)
        caption = await _caption_image(blob, ext)
        body_md = (f"![[{_ASSETS}/{asset.name}]]\n\n{caption}"
                   if caption else f"![[{_ASSETS}/{asset.name}]]\n\n(uncaptioned)")
        node = await write_node(account_id=account_id, title=stem, body=body_md,
                                source_kind="image", source_ref=asset.name)
        return {"ok": True, "node": node, "kind": "image",
                "captioned": bool(caption)}

    if ext in _AUD_EXTS:
        # Voice/Whisper was deliberately dropped in Mark 4 (no local model
        # lane), but this branch survived the cut and still imported it. The
        # import raised ModuleNotFoundError, which the app-level handler turned
        # into an opaque 500 — while the Vault tile's file picker went on
        # advertising these extensions. Refuse clearly instead of crashing.
        raise HTTPException(
            status_code=415,
            detail="audio transcription is not available in this build",
        )

    raise HTTPException(status_code=415,
                        detail=f"unsupported type {ext or '(none)'} — "
                               "md/txt/pdf/docx/xlsx/pptx/image")


async def _caption_image(blob: bytes, ext: str) -> str:
    """Factual VLM caption via the dispatch lane (the eye's proven path).
    No provider → honest empty caption, never a fabricated one."""
    try:
        import base64
        from .providers import dispatch
        provider = dispatch.get_provider()
        if provider is None:
            return ""
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
        data_url = f"data:{mime};base64," + base64.b64encode(blob).decode("ascii")
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text":
                    "Describe this image factually for a knowledge archive: "
                    "what it shows, any text it contains (transcribe exactly), "
                    "diagrams/labels. No speculation, no style commentary. "
                    "<=150 words."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }]
        model = dispatch.resolve_model("smart")
        text = await provider.complete(messages, model=model,
                                       temperature=0.1, max_tokens=300)
        return (text or "").strip()
    except Exception as e:
        print(f"[vault] caption failed (non-fatal): {type(e).__name__}", flush=True)
        return ""


class QueryIn(BaseModel):
    question: str
    k: int = 8
    model: str | None = None
    synthesize: bool = True


@router.post("/query")
async def vault_query(body: QueryIn, account_id: str = Depends(_account_id_dep)) -> dict:
    await scan(account_id=account_id)
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="empty question")
    hits = await retrieve(q, max(2, min(16, body.k)), account_id=account_id)
    sources = [{"node_id": h["node_id"], "slug": h.get("slug", ""),
                "title": h.get("title", ""), "domain": h.get("domain", []),
                "score": round(h.get("rrf", 0.0), 4)} for h in hits]
    answer = ""
    if body.synthesize and hits:
        from .providers import chat as chat_registry
        name, prov = chat_registry.provider_for_model(body.model or "")
        if prov is not None:
            blocks = "\n\n".join(
                f"[[{h.get('slug','?')}]] ({h.get('title','')})\n{h['text'][:1200]}"
                for h in hits
            )
            messages = [
                {"role": "system", "content":
                    "Answer from the user's own knowledge vault. Use ONLY the "
                    "provided notes; cite as [[slug]] inline. If the notes "
                    "don't cover it, say exactly what's missing — never fill "
                    "gaps from general knowledge without flagging it."},
                {"role": "user", "content": f"QUESTION: {q}\n\nNOTES:\n{blocks}"},
            ]
            try:
                async for chunk in prov.stream(model=body.model or "",
                                               messages=messages,
                                               no_think=True, temperature=0.2):
                    answer += chunk
                from . import spend
                spend.record(provider=name or "chat", model=body.model or "",
                             in_tok=sum(len(m["content"]) for m in messages) // 4,
                             out_tok=len(answer) // 4)
            except Exception:
                answer = ""  # sources still returned — honest degradation
    return {"answer": answer.strip(), "sources": sources, "hits": len(hits)}


@router.get("/tree")
async def vault_tree(account_id: str = Depends(_account_id_dep)) -> dict:
    await scan(account_id=account_id)
    with _LOCK:
        rows = _db().execute(
            "SELECT id, slug, title, domain, chars, updated FROM nodes "
            "WHERE owner_account_id=? ORDER BY domain, title",
            (account_id,)
        ).fetchall()
    tree: dict[str, Any] = {}
    for nid, slug, title, dj, chars, updated in rows:
        parts = json.loads(dj)
        cur = tree
        for p in parts:
            cur = cur.setdefault("children", {}).setdefault(p, {})
        cur.setdefault("notes", []).append(
            {"id": nid, "slug": slug, "title": title,
             "chars": chars, "updated": updated})
    return {"tree": tree.get("children", {}), "count": len(rows)}


@router.get("/graph")
async def vault_graph(limit: int = Query(1500, ge=10, le=5000),
                      account_id: str = Depends(_account_id_dep)) -> dict:
    await scan(account_id=account_id)
    with _LOCK:
        db = _db()
        nodes = db.execute(
            "SELECT id, slug, title, domain, chars FROM nodes "
            "WHERE owner_account_id=? ORDER BY updated DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
        ids = {n[0] for n in nodes}
        edges = [
            {"src": s, "dst": d, "kind": k}
            for s, d, k in db.execute("SELECT src, dst, kind FROM edges WHERE owner_account_id=?", (account_id,)).fetchall()
            if s in ids and d in ids
        ]
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["src"]] = deg.get(e["src"], 0) + 1
        deg[e["dst"]] = deg.get(e["dst"], 0) + 1
    return {
        "nodes": [{"id": n[0], "slug": n[1], "title": n[2],
                   "domain": json.loads(n[3]), "chars": n[4],
                   "degree": deg.get(n[0], 0)} for n in nodes],
        "edges": edges,
    }


@router.get("/node/{node_id}")
async def vault_node(node_id: str, account_id: str = Depends(_account_id_dep)) -> dict:
    with _LOCK:
        row = _db().execute(
            "SELECT id, slug, title, rel_path, domain, tags, source_kind, "
            "source_ref, created, updated FROM nodes "
            "WHERE owner_account_id=? AND (id=? OR slug=?)",
            (account_id, node_id, node_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="unknown note")
    path = vault_dir(account_id) / row[3]
    try:
        _meta, body = _parse_note(path.read_text("utf-8", errors="replace"))
    except Exception:
        raise HTTPException(status_code=404, detail="note file missing")
    return {"id": row[0], "slug": row[1], "title": row[2], "rel_path": row[3],
            "domain": json.loads(row[4]), "tags": json.loads(row[5]),
            "source": {"kind": row[6], "ref": row[7]},
            "created": row[8], "updated": row[9], "body": body}


@router.get("/search")
async def vault_search(q: str = Query(..., min_length=1, max_length=200),
                       k: int = Query(10, ge=1, le=30),
                       account_id: str = Depends(_account_id_dep)) -> dict:
    await scan(account_id=account_id)
    hits = await retrieve(q, k, account_id=account_id)
    out = {"items": [{"node_id": h["node_id"], "slug": h.get("slug", ""),
                      "title": h.get("title", ""),
                      "domain": h.get("domain", []),
                      "snippet": h["text"][:240],
                      "score": round(h.get("rrf", 0.0), 4)} for h in hits]}
    # SAY WHEN HALF THE SEARCH DID NOT RUN.
    #
    # If the query could not be embedded, retrieve() silently falls back to
    # keyword-only — and for a question phrased in words the note does not
    # contain, keyword finds nothing. The user sees an empty result and
    # reasonably concludes their note is missing. It is not; the search was.
    # A provider rate limit is the common cause and it is transient, so this
    # says so rather than looking like an empty vault.
    why = last_embed_failure()
    if why:
        out["degraded"] = True
        out["degraded_reason"] = (
            "Semantic search was unavailable for this query, so only keyword "
            f"matching ran. This is usually a transient provider rate limit. ({why})"
        )
    return out


@router.get("/stats")
async def vault_stats(account_id: str = Depends(_account_id_dep)) -> dict:
    await scan(account_id=account_id)
    with _LOCK:
        db = _db()
        nodes, chars = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(chars),0) FROM nodes WHERE owner_account_id=?", (account_id,)).fetchone()
        chunks = db.execute("SELECT COUNT(*) FROM chunks WHERE owner_account_id=?", (account_id,)).fetchone()[0]
        edges = db.execute("SELECT COUNT(*) FROM edges WHERE owner_account_id=?", (account_id,)).fetchone()[0]
        domains = len({json.loads(r[0])[0] for r in
                       db.execute("SELECT domain FROM nodes WHERE owner_account_id=?",
                                  (account_id,)).fetchall()
                       if json.loads(r[0])})
    return {"nodes": nodes, "chunks": chunks, "edges": edges,
            "domains": domains, "chars": chars,
            "dir": str(vault_dir(account_id))}


async def backfill_embeddings(batch: int = 128, *, account_id: str) -> dict:
    """Give a vector to every chunk that lacks a usable one.

    `scan()` cannot do this. It reconciles by mtime, so a note whose file has
    not changed is never revisited — and every chunk written while no embedding
    provider was configured has `vec IS NULL` on an unchanged file. Without this,
    configuring a provider fixes nothing that already exists: new notes get
    vectors, the entire existing corpus stays invisible to vector search, and
    nothing reports the gap.

    Also re-embeds chunks stamped with a DIFFERENT model or dimension, which is
    what makes changing provider a supported operation rather than a silent
    corruption — those rows are skipped by _vec_matrix() until they are redone.
    """
    from .providers.embeddings import EmbedError, active_dim, active_model, embed_texts

    model, dim = active_model(), active_dim()
    if not model or not dim:
        raise HTTPException(
            status_code=503,
            detail="no embedding provider configured; set VOYAGE_API_KEY first",
        )

    done = failed = 0
    while True:
        with _LOCK:
            rows = _db().execute(
                "SELECT id, text FROM chunks "
                "WHERE owner_account_id = ? AND (vec IS NULL OR model IS NOT ? OR dim IS NOT ?) "
                "LIMIT ?",
                (account_id, model, dim, batch),
            ).fetchall()
        if not rows:
            break

        texts = [(r[1] or "").strip() for r in rows]
        keep = [(r[0], t) for r, t in zip(rows, texts) if t]
        # An empty chunk can never be embedded; stamp it so the query above
        # stops returning it, or this loop never terminates.
        empties = [r[0] for r, t in zip(rows, texts) if not t]
        if empties:
            with _LOCK:
                db = _db()
                db.executemany(
                    "UPDATE chunks SET vec=NULL, model=?, dim=?"
                    " WHERE id=? AND owner_account_id=?",
                    [(model, dim, cid, account_id) for cid in empties],
                )
                db.commit()

        if keep:
            try:
                vecs = await embed_texts([t for _, t in keep], input_type="document")
            except EmbedError as e:
                failed += len(keep)
                print(f"[vault] backfill stopped after {done}: {e}", flush=True)
                break
            with _LOCK:
                db = _db()
                db.executemany(
                    "UPDATE chunks SET vec=?, model=?, dim=?"
                    " WHERE id=? AND owner_account_id=?",
                    [(v.astype(np.float32).tobytes(), model, dim, cid, account_id)
                     for (cid, _), v in zip(keep, vecs)],
                )
                db.commit()
            done += len(keep)

        if len(rows) < batch:
            break

    if done:
        _mark_dirty()
    return {"embedded": done, "failed": failed, "model": model, "dim": dim}


@router.post("/reindex")
async def vault_reindex(account_id: str = Depends(_account_id_dep)) -> dict:
    """Reconcile files against the index, then fill in any missing vectors.

    Both halves are needed. scan() alone leaves every pre-existing chunk without
    a vector forever, because it only revisits files whose mtime changed.
    """
    out = await scan(force=True, account_id=account_id)
    from .providers.embeddings import is_configured
    if is_configured():
        out["backfill"] = await backfill_embeddings(account_id=account_id)
    else:
        out["backfill"] = {"skipped": "no embedding provider configured"}
    return out


@router.get("/embeddings/status")
async def vault_embeddings_status(account_id: str = Depends(_account_id_dep)) -> dict:
    """How much of the corpus is actually searchable by vector.

    "Embeddings are on" and "the corpus is embedded" are different facts, and
    the gap between them is invisible everywhere else.
    """
    from .providers.embeddings import active_dim, active_model, status
    model, dim = active_model(), active_dim()
    with _LOCK:
        total = _db().execute("SELECT COUNT(*) FROM chunks WHERE owner_account_id=?",
                              (account_id,)).fetchone()[0]
        usable = _db().execute(
            "SELECT COUNT(*) FROM chunks WHERE owner_account_id=? AND vec IS NOT NULL"
            " AND model IS ? AND dim IS ?",
            (account_id, model, dim),
        ).fetchone()[0]
    return {
        "provider": status(),
        "chunks_total": total,
        "chunks_with_usable_vector": usable,
        "chunks_needing_backfill": total - usable,
        "hint": (None if total == usable else
                 "POST /vault/reindex to embed the rest"),
    }
