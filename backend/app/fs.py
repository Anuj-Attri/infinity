"""Filesystem endpoints for the Code tile, confined to one workspace.

GET    /fs/tree[?path=<dir>]              — recursive listing, depth-limited
GET    /fs/read?path=<file>               — contents + a version tag
POST   /fs/write {path,content,           — overwrite, refusing a stale version
                  expect_version?}
GET    /fs/entry-info?path=<p>            — what deleting this would destroy
DELETE /fs/entry?path=<p>[&recursive]     — remove a file or a directory
POST   /fs/rename {path,to}               — move within the workspace

EVERY path is confined to WORKSPACE_ROOT. There is no way to address anything
outside it, and no request can widen it.

THE LAST THREE ARE DESTRUCTIVE, and that changes what `_safe_resolve` is. It was
written as a READ confinement, where a bug discloses a file. Behind `unlink` and
`rename` the same bug destroys work permanently — there is no trash, no git and
no backup in this product — or moves data into another account's workspace,
which is a sibling directory on the same volume.

Nothing about the function needed to change: resolve-then-contain is already the
right order, and it already follows symlinks BEFORE testing, so the check
applies to what will actually be touched. What changed is the standard of proof.
`test_fs_destructive.py` re-runs the entire escape battery against delete and
rename rather than assuming that passing it for `read` implies passing it for
`unlink`, and every guard in this file is mutation-tested there.

Two things worth knowing before editing this module:
  * `rename` is the only route taking TWO caller-supplied paths. Validating only
    the source leaves an arbitrary WRITE primitive.
  * the tree HIDES dotfiles and skips node_modules/.git/dist, so a directory can
    render as empty and hold a great deal. `/fs/entry-info` exists so the
    confirmation prompt can tell the truth rather than repeat that omission.

WHAT THIS REPLACED, AND WHY IT HAD TO GO. This module was lifted verbatim from
the desktop build, where it served the developer's own machine to the developer
— a reasonable thing for a local tool to do. Hosted, the same code was an
arbitrary read/write primitive on the container:

  * `_ROOTS` was a process-global set, so it was shared by every user rather
    than scoped to anyone.
  * `/fs/tree?path=<anything>` ADDED whatever directory it was given to that
    set. Naming a directory was the same as being granted it.
  * `/fs/read` auto-registered a file's parent when it was not already covered,
    so reading a file WIDENED what could later be written.
  * `/fs/write` then permitted anything under any registered root.

Chained, that is: `GET /fs/tree?path=/srv` then `POST /fs/write` over
`/srv/dist/index.html` — persistent XSS served to every visitor from the app's
own origin — plus read access to `.env`, `tokens.json` and `vault.db`. The
container runs as root. The tree also deliberately un-hid `.env`.

The replacement has no session state and nothing to widen: resolve, then require
the result to be inside WORKSPACE_ROOT. `Path.resolve()` collapses `..` and
follows symlinks before the check, so neither escapes.

Refusals are a uniform 403 whether or not the path exists, so this cannot be
used to probe the container's filesystem layout.

PER-ACCOUNT since 2026-08-23. Code files are FILES, so ownership has to be a
directory: a shared workspace means two accounts editing `main.py` edit the SAME
file, and every path check here would pass while the content was shared. M6 moves
this to cloud storage; the per-account SHAPE is already what it needs to be.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .paths import data_path

log = logging.getLogger(__name__)

router = APIRouter(prefix="/fs", tags=["fs"])

MAX_FILE_BYTES = 5 * 1024 * 1024     # 5 MB per file — typical text/code
MAX_TREE_ENTRIES = 5000              # hard cap on a single /tree response
MAX_TREE_DEPTH = 6                   # depth from the root
SKIP_DIR_NAMES = {
    ".git", "node_modules", "target", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", "dist", "build", ".next", ".turbo", ".cache",
    "site-packages",
}

# The one directory these endpoints can ever touch. Goes through data_path() so
# it lands on the mounted volume with the rest of the state — a workspace on the
# container's own disk would be silently erased by every redeploy.
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_DIR") or data_path("workspace"))


def workspace_dir(account_id: str) -> Path:
    """ONE ACCOUNT'S workspace, created on demand.

    Code files, like vault notes, are FILES — so ownership has to be a
    directory. A single shared workspace means two accounts editing `main.py`
    edit the SAME file: every path check in this module would pass, and the
    content would still be shared. That is the failure a containment check
    cannot catch, because nothing about it is out of bounds.

    Resolved every call rather than cached: the containment test below is only
    sound if both sides went through the same normalisation, and a cached
    unresolved root compares wrongly the moment the path involves a symlink —
    which on macOS it does (/tmp -> /private/tmp) and in a container it can.
    """
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    _migrate_flat_workspace()
    d = WORKSPACE_ROOT / account_id
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


_flat_migrated = False


def _migrate_flat_workspace() -> None:
    """Move pre-ownership files from <root>/ into <root>/<single tenant>/.

    Honest only while there IS one tenant. Doing it later would be a guess about
    whose files these were.
    """
    global _flat_migrated
    if _flat_migrated:
        return
    _flat_migrated = True
    from .accounts import SINGLE_TENANT
    target = WORKSPACE_ROOT / SINGLE_TENANT.id
    strays = [c for c in WORKSPACE_ROOT.iterdir() if not _is_account_dir(c)]
    if not strays:
        return
    target.mkdir(parents=True, exist_ok=True)
    import shutil
    moved = 0
    for c in strays:
        dest = target / c.name
        if dest.exists():
            continue                 # already migrated; never overwrite
        shutil.move(str(c), str(dest))
        moved += 1
    if moved:
        print(f"[fs] moved {moved} pre-ownership item(s) into {target.name}/", flush=True)


def _is_account_dir(p: Path) -> bool:
    """A uuid-shaped directory name is an account, not a user file."""
    return p.is_dir() and len(p.name) == 36 and p.name.count("-") == 4


def _safe_resolve(raw: str | None, *, account_id: str) -> Path:
    """Resolve a caller-supplied path, or refuse.

    The ONLY authorisation in this module. `resolve()` first, so `..` is
    collapsed and symlinks are followed BEFORE the containment test — checking a
    raw string would let `workspace/../../etc/passwd` or a symlink out of the
    workspace both pass. Since the root is now per-account, the same test also
    stops one account reaching into another's.

    IT MUST NOT MUTATE THE PATH. It used to do `raw = raw.strip()` before
    building the Path, and `str.strip()` removes every unicode space — including
    U+00A0, which is invisible. With `nb` and `nb ` both in a workspace, the
    tree hands out both names verbatim, and a request naming the second one
    silently RESOLVED TO THE FIRST. Reproduced end to end: asking to delete
    `nb ` returned `{"ok":true,"path":"nb","deleted":1}` and destroyed `nb`,
    while the directory actually named in the request survived. The confirmation
    dialog shows the client's own string, whose trailing space is invisible, so
    the user reads the right name and loses the other folder.

    Worth stating precisely, because it is easy to file under the wrong heading:
    this was NOT a containment escape. Stripping only ever NARROWS reach — every
    whitespace-padded traversal still refused, because trimming a leading space
    turns a relative string into an absolute one that containment then rejects.
    It was silent, irreversible deletion of the caller's own data, in their own
    workspace. That is worse than it sounds and better than it looks.

    `strip()` survives in the emptiness test on the next line, where it is only
    asking "did they send anything", and cannot retarget anything.
    """
    root = workspace_dir(account_id)
    if not raw or not raw.strip():
        return root
    try:
        candidate = Path(raw)
        # A relative path is relative to the workspace, never to the process's
        # working directory, which is the container root.
        p = (candidate if candidate.is_absolute() else root / candidate).expanduser()
        p = p.resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad path: {e}")

    if p != root and root not in p.parents:
        # Identical response whether or not it exists: a distinguishable 404
        # would turn this into a filesystem-layout probe.
        raise HTTPException(
            status_code=403,
            detail="path is outside the workspace",
        )
    return p


def _account_id_dep() -> str:
    """Whose workspace this request touches.

    A dependency rather than a plain argument: FastAPI would read a bare
    `account_id: str` off the QUERY STRING, letting the caller name the account
    whose files they wanted — the exact opposite of the point.
    """
    from .accounts import SINGLE_TENANT
    return SINGLE_TENANT.id


def _assert_inside(p: Path, *, account_id: str) -> None:
    """Re-check that an already-resolved path belongs to this account.

    The helpers below take a `Path` that a caller is supposed to have obtained
    from `_safe_resolve`. "Supposed to" is a convention, and a convention is
    what the next person forgets — so it is checked here too. Cheap, and it
    turns "the caller authorised this" from a comment into an invariant.

    Raising rather than returning: there is no sensible fallback. A path outside
    the workspace reaching a hashing or counting helper means the authorisation
    step was skipped, and continuing would compute an honest-looking answer
    about someone else's file.
    """
    root = workspace_dir(account_id).resolve()
    rp = p.resolve()
    if rp != root and root not in rp.parents:
        raise HTTPException(status_code=403, detail="path is outside the workspace")


def version_of(p: Path, *, account_id: str) -> str:
    """A version tag for one file: sha256 of its BYTES on disk.

    Content, not mtime+size. mtime is the cheaper answer and the wrong one here:

      * filesystem timestamp granularity varies (some are whole seconds), so two
        writes inside the same tick are indistinguishable — and autosave writes
        in bursts, which is exactly that case;
      * a volume restored from a backup, or a container restart, can move mtimes
        without the content changing, turning every open file into a false
        conflict;
      * mtime is metadata a future sync or backup process can touch.

    A hash costs one read of a file already capped at 5 MB, on an operation that
    is already doing filesystem I/O. It says precisely what we mean: the bytes
    are, or are not, what you last saw.

    It has one honest weakness — A -> B -> A hashes the same, so a change that
    was undone reads as no change. That is the correct answer for a
    lost-UPDATE check: the bytes you are about to overwrite are the bytes you
    started from, so nothing is lost.

    A missing file is "" rather than an error: that is the version a client
    holds for a file it is about to create, and it makes "create only if it does
    not exist yet" expressible without a separate route.
    """
    _assert_inside(p, account_id=account_id)
    try:
        return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""
    except OSError as e:
        # Never fail a save because the version could not be computed — but do
        # not return a value that would compare EQUAL to anything either.
        log.warning("version_of %s failed: %s", p, e)
        return "unreadable"


def _looks_binary(p: Path) -> bool:
    mt, _ = mimetypes.guess_type(str(p))
    if mt and (mt.startswith(("text/", "application/json", "application/xml",
                              "application/javascript", "application/typescript"))):
        return False
    # Sample the first 4KB for NUL bytes — cheap binary detector.
    try:
        with p.open("rb") as f:
            chunk = f.read(4096)
        if b"\x00" in chunk:
            return True
        # Decode-ability check: if it decodes cleanly as utf-8, treat as text.
        try:
            chunk.decode("utf-8")
            return False
        except UnicodeDecodeError:
            return True
    except OSError:
        return True


# -------------------------------------------------- /tree -----------------

WORKSPACE_LABEL = "Workspace"


def rel_path(p: Path, *, account_id: str) -> str:
    """A path as the CLIENT should see it: relative to the workspace, POSIX.

    The browser used to receive `str(p)` — the absolute server path, which on a
    hosted deployment looks like `/data/workspaces/<account-uuid>/src/main.py`.
    That did three things, none of them wanted:

      * it printed the account UUID in the file tree, because the workspace
        directory IS named after the account (the owner's screenshot showed
        exactly that as the root folder's name);
      * it published the server's filesystem layout to anyone signed in; and
      * it made every path the client echoed back to /read and /write an
        absolute one, so the containment check in `_safe_resolve` was the only
        thing standing between a typo and a path traversal.

    Confinement has not changed and still lives in `_safe_resolve` — this is
    defence in depth, not a replacement for it. But a client that has never been
    told an absolute path cannot send one back by accident.

    The workspace root is "" rather than "." or "/": empty is what
    `_safe_resolve` already maps to the root.
    """
    root = workspace_dir(account_id)
    try:
        r = p.resolve().relative_to(root.resolve())
    except ValueError:
        # Outside the workspace. Unreachable via _safe_resolve, but returning a
        # raw absolute path as a fallback would defeat the whole point.
        return ""
    return "" if str(r) == "." else r.as_posix()


def _build_tree(root: Path, *, account_id: str) -> dict[str, Any]:
    """Depth-limited recursive directory listing. Skips noisy folders.
    Returns a single dict shaped like:
        { name, path, kind: 'dir', children: [...] }
        { name, path, kind: 'file', size }

    `path` is workspace-relative (see rel_path); `name` of the root is a label,
    never the directory's real name — that name is the account's UUID.
    """
    count = 0
    ws = workspace_dir(account_id)

    def visit(p: Path, depth: int) -> dict[str, Any] | None:
        nonlocal count
        if count >= MAX_TREE_ENTRIES:
            return None
        count += 1
        try:
            if p.is_dir():
                name = WORKSPACE_LABEL if p.resolve() == ws.resolve() else p.name
                node = {"name": name, "path": rel_path(p, account_id=account_id),
                        "kind": "dir", "children": []}
                if depth >= MAX_TREE_DEPTH:
                    return node
                try:
                    entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
                except (PermissionError, OSError):
                    return node
                for child in entries:
                    if child.is_dir() and child.name in SKIP_DIR_NAMES:
                        continue
                    if child.name.startswith("."):
                        # Dotfiles stay hidden, and `.env` is NOT excepted any
                        # more. The desktop build surfaced it deliberately, which
                        # made sense when the tree was the developer's own repo
                        # on their own machine. Hosted, an env file is the single
                        # highest-value file in the tree.
                        if child.is_dir() and child.name not in (".github",):
                            continue
                        if not child.is_dir():
                            continue
                    sub = visit(child, depth + 1)
                    if sub:
                        node["children"].append(sub)
                return node
            else:
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                return {"name": p.name, "path": rel_path(p, account_id=account_id),
                        "kind": "file", "size": size}
        except OSError as e:
            log.warning("tree visit %s failed: %s", p, e)
            return None

    return visit(root, 0) or {"name": WORKSPACE_LABEL, "path": "",
                              "kind": "dir", "children": []}


@router.get("/tree")
async def tree(path: str | None = Query(None),
               account_id: str = Depends(_account_id_dep)) -> dict:
    """List the workspace. `path` is optional and defaults to the root.

    It no longer takes a root FROM the caller — supplying one used to grant it.
    A path may still be passed to list a subdirectory, and it is confined.
    """
    p = _safe_resolve(path, account_id=account_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="path does not exist")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    # `root` used to be the absolute server path. Nothing in the client needed
    # it, and it named the account. It is the relative root now, i.e. "".
    return {"root": rel_path(p, account_id=account_id),
            "tree": _build_tree(p, account_id=account_id)}


# -------------------------------------------------- /read -----------------

@router.get("/read")
async def read(path: str = Query(...),
               account_id: str = Depends(_account_id_dep)) -> dict:
    p = _safe_resolve(path, account_id=account_id)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="not a file")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large: {p.stat().st_size} > {MAX_FILE_BYTES}")
    if _looks_binary(p):
        raise HTTPException(status_code=415, detail="file looks binary; refusing to load")
    try:
        # newline="" matters. `read_text` defaults to UNIVERSAL NEWLINES, which
        # silently turns every CRLF on disk into LF in the string we hand the
        # editor. /write then writes it back with newline="" — no translation —
        # so opening a Windows-authored file and saving it without touching a
        # character rewrote every line ending in it. Nothing said so.
        #
        # This also had to be fixed before any version/ETag scheme could work:
        # a file that cannot round-trip read -> write unchanged hashes
        # differently every time, so every save would look like a conflict.
        with p.open("r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=415, detail=f"not utf-8 text: {e}")
    return {"path": rel_path(p, account_id=account_id), "content": text,
            "size": len(text), "version": version_of(p, account_id=account_id)}


# -------------------------------------------------- /write ----------------

class WriteIn(BaseModel):
    path: str
    content: str
    # The version the client believes it is editing, from /read or a previous
    # /write. When present and stale, the write is REFUSED — see version_of.
    #
    # Optional, because a caller that has not read the file has no version to
    # send and forcing one would only make it invent a bogus value. The client
    # always sends it. A missing one is last-writer-wins, and the log line at
    # the end of this handler records WHICH guard applied, so an unguarded
    # overwrite is greppable after the fact rather than indistinguishable.
    #
    # `""` is a real value here, not "absent": it is the version of a file that
    # does not exist, so sending it means "create this, and refuse if something
    # is already there". The new-file control sends exactly that — without it,
    # typing the name of an existing file silently truncated it to zero bytes.
    expect_version: str | None = None


@router.post("/write")
async def write(body: WriteIn,
                account_id: str = Depends(_account_id_dep)) -> dict:
    # _safe_resolve is the whole authorisation. It already refused anything
    # outside the workspace, and nothing a request can do widens that.
    p = _safe_resolve(body.path, account_id=account_id)
    if p == workspace_dir(account_id):
        raise HTTPException(status_code=400, detail="that is the workspace root, not a file")
    if len(body.content.encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="content too large")
    if p.is_dir():
        raise HTTPException(status_code=400, detail="path is a directory")

    # Lost-update check. Without it, two tiles on the same file — and `code` is a
    # multi-instance kind, so that needs no second device — silently overwrite
    # each other, and whoever saved first never learns their work is gone.
    #
    # This is a CHECK-THEN-WRITE, so there is a window between hashing and
    # replacing. It is not closed here and cannot be with POSIX rename alone;
    # what it buys is catching the realistic case (a human editing in two
    # places, seconds or minutes apart) rather than a microsecond race that
    # needs two writes interleaved inside the same request.
    if body.expect_version is not None:
        current = version_of(p, account_id=account_id)
        if current != body.expect_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "changed_on_server",
                    "message": "This file changed since you opened it.",
                    "current_version": current,
                    # NOT the file's contents: a 409 must not become a way to
                    # read a file the caller could not otherwise read. They can
                    # read it — same confinement — but the response should say
                    # what happened, not smuggle data.
                },
            )

    # Create intermediate directories so a new file in a new folder works. Safe:
    # p is already known to be inside the workspace.
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"cannot create parent: {e}")
    try:
        # Atomic-ish: write to .tmp then rename.
        tmp = p.with_suffix(p.suffix + ".infinity.tmp")
        tmp.write_text(body.content, encoding="utf-8", newline="")
        tmp.replace(p)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    # The only mutating /fs route that had no audit line at all — delete and
    # rename both log. `guard` records what the write was allowed BY, so an
    # unguarded overwrite can be found afterwards instead of looking identical
    # to a checked one. Paths only; never the content.
    guard = ("create" if body.expect_version == ""
             else "version" if body.expect_version is not None
             else "unguarded")
    log.info("fs write account=%s path=%s bytes=%d guard=%s",
             account_id, rel_path(p, account_id=account_id),
             len(body.content.encode("utf-8")), guard)
    return {"ok": True, "path": rel_path(p, account_id=account_id),
            "size": len(body.content), "version": version_of(p, account_id=account_id)}


# -------------------------------------------------- destructive -----------
#
# Everything below can LOSE WORK. There is no trash, no git, and no backup in
# this product, so a mistake here is permanent.
#
# `_safe_resolve` was written as a READ confinement: get it wrong and a file
# leaks. These routes make the same function a destructive-capability gate,
# where the same bug deletes someone's work or moves it into another account.
# Nothing about it needed to change — resolve-then-contain is already the right
# order, and it already resolves symlinks BEFORE testing containment, so the
# check applies to what will actually be touched rather than to the name of it.
# What changed is the standard of proof: test_fs_destructive.py re-runs the
# whole escape suite against delete and rename rather than assuming that passing
# it for read implies passing it for unlink.

# How many entries a single recursive delete may remove. Not a security control
# — it is a blast radius. Someone who means to delete a scratch folder and hits
# the wrong row should be stopped by the size of what they are about to lose.
MAX_RECURSIVE_DELETE = 500


def _count_entries(root: Path, *, account_id: str) -> int:
    """Everything under `root`, INCLUDING what the tree hides.

    This number is shown to the user before a recursive delete, so it must count
    what will actually be destroyed rather than what they can see. `_build_tree`
    hides dotfiles and skips SKIP_DIR_NAMES (node_modules, .git, dist, ...), so a
    directory can look empty in the UI and hold a hundred thousand files — and
    "delete this empty-looking folder" is the single likeliest way to lose work
    here.
    """
    _assert_inside(root, account_id=account_id)
    n = 0
    for _, dirnames, filenames in os.walk(root):
        n += len(dirnames) + len(filenames)
        if n > MAX_RECURSIVE_DELETE * 4:      # enough to report "far too many"
            break
    return n


@router.get("/entry-info")
async def entry_info(path: str = Query(...),
                     account_id: str = Depends(_account_id_dep)) -> dict:
    """What deleting this would actually destroy. Read-only.

    Exists so the UI can tell the truth in its confirmation prompt instead of
    guessing from a tree that deliberately hides things.
    """
    p = _safe_resolve(path, account_id=account_id)
    if p == workspace_dir(account_id):
        raise HTTPException(status_code=400, detail="that is the workspace root")
    if not p.exists():
        raise HTTPException(status_code=404, detail="not found")
    if p.is_file():
        return {"path": rel_path(p, account_id=account_id), "kind": "file", "entries": 0}
    n = _count_entries(p, account_id=account_id)
    return {
        "path": rel_path(p, account_id=account_id),
        "kind": "dir",
        "entries": n,
        "hidden_from_tree": n > 0 and not any(
            not c.name.startswith(".") and c.name not in SKIP_DIR_NAMES
            for c in p.iterdir()
        ),
        "over_limit": n > MAX_RECURSIVE_DELETE,
        "limit": MAX_RECURSIVE_DELETE,
    }


@router.delete("/entry")
async def delete_entry(path: str = Query(...),
                       recursive: bool = Query(False),
                       account_id: str = Depends(_account_id_dep)) -> dict:
    p = _safe_resolve(path, account_id=account_id)
    root = workspace_dir(account_id)

    # Belt and braces. _safe_resolve already refuses anything outside the
    # workspace; this refuses the workspace ITSELF, which is inside it and would
    # otherwise pass every check and delete the account's entire workspace.
    if p == root:
        raise HTTPException(status_code=400, detail="the workspace itself cannot be deleted")

    if not p.exists():
        # 404, not a silent success. Deleting is destructive and the caller
        # should learn that its idea of the tree is stale rather than be told
        # "done" about something that was not there — the same stale view might
        # be about to delete the wrong thing next.
        raise HTTPException(status_code=404, detail="not found")

    rel = rel_path(p, account_id=account_id)

    if p.is_file() or p.is_symlink():
        try:
            p.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"delete failed: {e}")
        log.info("fs delete file account=%s path=%s", account_id, rel)
        return {"ok": True, "path": rel, "kind": "file", "deleted": 1}

    entries = _count_entries(p, account_id=account_id)
    if entries and not recursive:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "directory_not_empty",
                "message": f"{rel} contains {entries} item(s).",
                "entries": entries,
                "over_limit": entries > MAX_RECURSIVE_DELETE,
            },
        )
    if entries > MAX_RECURSIVE_DELETE:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "too_many_entries",
                "message": (f"{rel} contains {entries} item(s), more than the "
                            f"{MAX_RECURSIVE_DELETE} this will remove at once. "
                            f"Delete some of it first."),
                "entries": entries,
                "limit": MAX_RECURSIVE_DELETE,
            },
        )
    try:
        shutil.rmtree(p)
    except OSError as e:
        # A recursive delete that fails part-way leaves the workspace in a state
        # neither side predicted, so say so plainly rather than reporting a
        # clean failure the caller might retry blindly.
        log.warning("fs delete PARTIAL account=%s path=%s: %s", account_id, rel, e)
        raise HTTPException(
            status_code=500,
            detail=f"delete failed part-way through {rel}; some of it may be gone: {e}")
    log.info("fs delete dir account=%s path=%s entries=%d", account_id, rel, entries)
    return {"ok": True, "path": rel, "kind": "dir", "deleted": entries}


class RenameIn(BaseModel):
    path: str
    to: str


@router.post("/rename")
async def rename(body: RenameIn,
                 account_id: str = Depends(_account_id_dep)) -> dict:
    """Move a file or directory to another path inside the workspace.

    BOTH endpoints go through _safe_resolve. Validating only the source would
    make this an arbitrary WRITE primitive: `to` is a path the caller supplies,
    and an unvalidated one moves the file anywhere the process can reach —
    including another account's workspace, which is a sibling directory.
    """
    root = workspace_dir(account_id)
    src = _safe_resolve(body.path, account_id=account_id)
    dst = _safe_resolve(body.to, account_id=account_id)

    if src == root or dst == root:
        raise HTTPException(status_code=400, detail="the workspace itself cannot be moved")
    if not src.exists():
        raise HTTPException(status_code=404, detail="not found")
    if src == dst:
        return {"ok": True, "from": rel_path(src, account_id=account_id),
                "to": rel_path(dst, account_id=account_id), "unchanged": True}

    # Moving a directory INTO ITSELF. Both paths are inside the workspace, so
    # both pass every containment check — and `os.replace` either fails with a
    # confusing errno or, on some platforms, leaves the tree unreachable.
    if src.is_dir() and src in dst.parents:
        raise HTTPException(status_code=400, detail="cannot move a folder into itself")

    if dst.exists():
        # Never overwrite. This is also the case-insensitive trap: on Linux
        # "README.md" -> "readme.md" is a move to a NEW path, while on
        # Windows/macOS it is the SAME file, so `dst.exists()` is true and this
        # refuses a rename the user reasonably expects to work. Refusing is the
        # safe half of that trade — the alternative silently destroys the file
        # being renamed — and the message says which case it is.
        if str(src).lower() == str(dst).lower():
            raise HTTPException(
                status_code=409,
                detail="only the capitalisation differs, and this filesystem "
                       "does not distinguish it — rename it to something else first")
        raise HTTPException(status_code=409, detail="something is already there")

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # os.replace, not shutil.move: `move` falls back to copy+delete across a
        # filesystem boundary, which for a large tree is slow and can half-fail.
        # Inside one workspace there is no boundary to cross, so a rename that
        # cannot be atomic is a signal something is wrong and should raise.
        os.replace(src, dst)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"rename failed: {e}")

    log.info("fs rename account=%s from=%s to=%s", account_id,
             rel_path(src, account_id=account_id), rel_path(dst, account_id=account_id))
    return {"ok": True,
            "from": rel_path(src, account_id=account_id),
            "to": rel_path(dst, account_id=account_id)}
