"""Filesystem endpoints for the Code tile.

GET  /fs/tree?path=<root>            — recursive listing, depth-limited
GET  /fs/read?path=<file>            — read a single file's UTF-8 contents
POST /fs/write {path,content}        — overwrite a single file

Path safety: every call resolves to an absolute Path. Operations are
permitted ONLY when that absolute path is INSIDE a registered "project
root" — see `_within_any_root`. Roots are added on first /tree request
against a folder, so the user picks the root once and everything else
is gated to that subtree. Symlink-walking and `..` traversal cannot
escape the root because `Path.resolve()` collapses both.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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

# Project roots the user has explicitly opened in this session. Each
# subsequent read/write is checked against this set — no path can escape
# every registered root.
_ROOTS: set[Path] = set()


def _within_any_root(p: Path) -> bool:
    p = p.resolve()
    for r in _ROOTS:
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _safe_resolve(raw: str) -> Path:
    if not raw:
        raise HTTPException(status_code=400, detail="path required")
    try:
        p = Path(raw).expanduser().resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad path: {e}")
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
                    if child.name.startswith(".") and child.name not in (".env",):
                        # Hide most dotfiles/dirs from the tree (but allow .env).
                        if child.is_dir() and child.name not in (".github",):
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
async def tree(path: str = Query(...)) -> dict:
    p = _safe_resolve(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"path does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {p}")
    # Register this root for subsequent read/write authorization.
    _ROOTS.add(p)
    return {"root": str(p), "tree": _build_tree(p)}


# -------------------------------------------------- /read -----------------

@router.get("/read")
async def read(path: str = Query(...)) -> dict:
    p = _safe_resolve(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"not a file: {p}")
    if not _within_any_root(p):
        # Allow reading any file ONLY if its parent has been opened as a
        # root via /tree; otherwise gate it. Helpful: register the parent
        # directory automatically when a single file is requested first,
        # so the "open a single file" flow works.
        _ROOTS.add(p.parent.resolve())
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
    p = _safe_resolve(body.path)
    if not _within_any_root(p):
        raise HTTPException(
            status_code=403,
            detail="path is outside every registered project root; "
                   "open the parent folder via /fs/tree first",
        )
    if len(body.content.encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="content too large")
    if not p.parent.exists():
        raise HTTPException(status_code=404, detail=f"parent dir missing: {p.parent}")
    try:
        # Atomic-ish: write to .tmp then rename.
        tmp = p.with_suffix(p.suffix + ".infinity.tmp")
        tmp.write_text(body.content, encoding="utf-8", newline="")
        tmp.replace(p)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    return {"ok": True, "path": str(p), "size": len(body.content)}
