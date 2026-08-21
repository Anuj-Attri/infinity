"""Filesystem endpoints for the Code tile, confined to one workspace.

GET  /fs/tree[?path=<dir>]           — recursive listing, depth-limited
GET  /fs/read?path=<file>            — read a single file's UTF-8 contents
POST /fs/write {path,content}        — overwrite a single file

EVERY path is confined to WORKSPACE_ROOT. There is no way to address anything
outside it, and no request can widen it.

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

TODO(M6): WORKSPACE_ROOT becomes per-account cloud storage. It is deliberately a
single directory now — one shared workspace for one tenant is the honest shape
of today's single-password deployment, and it is not reachable outside the door.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
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


def workspace_root() -> Path:
    """Resolved workspace root, created on demand.

    Resolved every call rather than cached: the comparison below is only sound
    if both sides went through the same normalisation, and a cached unresolved
    root would compare wrongly the moment the path involves a symlink — which on
    macOS it does (/tmp -> /private/tmp) and in a container it can.
    """
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT.resolve()


def _safe_resolve(raw: str | None) -> Path:
    """Resolve a caller-supplied path, or refuse.

    The ONLY authorisation in this module. `resolve()` first, so `..` is
    collapsed and symlinks are followed BEFORE the containment test — checking a
    raw string would let `workspace/../../etc/passwd` or a symlink out of the
    workspace both pass.
    """
    root = workspace_root()
    if not raw or not raw.strip():
        return root
    try:
        raw = raw.strip()
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

def _build_tree(root: Path) -> dict[str, Any]:
    """Depth-limited recursive directory listing. Skips noisy folders.
    Returns a single dict shaped like:
        { name, path, kind: 'dir', children: [...] }
        { name, path, kind: 'file', size }
    """
    count = 0

    def visit(p: Path, depth: int) -> dict[str, Any] | None:
        nonlocal count
        if count >= MAX_TREE_ENTRIES:
            return None
        count += 1
        try:
            if p.is_dir():
                node = {"name": p.name or str(p), "path": str(p), "kind": "dir", "children": []}
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
                return {"name": p.name, "path": str(p), "kind": "file", "size": size}
        except OSError as e:
            log.warning("tree visit %s failed: %s", p, e)
            return None

    return visit(root, 0) or {"name": root.name or str(root), "path": str(root), "kind": "dir", "children": []}


@router.get("/tree")
async def tree(path: str | None = Query(None)) -> dict:
    """List the workspace. `path` is optional and defaults to the root.

    It no longer takes a root FROM the caller — supplying one used to grant it.
    A path may still be passed to list a subdirectory, and it is confined.
    """
    p = _safe_resolve(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="path does not exist")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    return {"root": str(workspace_root()), "tree": _build_tree(p)}


# -------------------------------------------------- /read -----------------

@router.get("/read")
async def read(path: str = Query(...)) -> dict:
    p = _safe_resolve(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="not a file")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large: {p.stat().st_size} > {MAX_FILE_BYTES}")
    if _looks_binary(p):
        raise HTTPException(status_code=415, detail="file looks binary; refusing to load")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=415, detail=f"not utf-8 text: {e}")
    return {"path": str(p), "content": text, "size": len(text)}


# -------------------------------------------------- /write ----------------

class WriteIn(BaseModel):
    path: str
    content: str


@router.post("/write")
async def write(body: WriteIn) -> dict:
    # _safe_resolve is the whole authorisation. It already refused anything
    # outside the workspace, and nothing a request can do widens that.
    p = _safe_resolve(body.path)
    if p == workspace_root():
        raise HTTPException(status_code=400, detail="that is the workspace root, not a file")
    if len(body.content.encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="content too large")
    if p.is_dir():
        raise HTTPException(status_code=400, detail="path is a directory")
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
    return {"ok": True, "path": str(p), "size": len(body.content)}
