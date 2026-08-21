"""Server-side document text extraction.

Returns BOTH plain text (for Nova's /slug context-injection) AND a
structured `view` payload the frontend renders as a table / slide stack /
paged document. The view is purely visual; Nova never sees it.

This is the READ path. Edit endpoints are a separate later rung.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Callable

from fastapi import APIRouter, File, HTTPException, UploadFile

log = logging.getLogger(__name__)


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
TABLE_ROW_CAP    = 500           # per-sheet cap in the rich view payload
SLIDE_CAP        = 200
PAGE_CAP         = 200


def _file_ext(name: str) -> str:
    _, dot, ext = name.rpartition(".")
    return ext.lower() if dot else ""


ExtractResult = tuple[str, dict[str, Any] | None]


def _render_table_rows(rows: list) -> list[str]:
    """Render a table as RETRIEVAL-FRIENDLY plain-text lines (used for the text
    layer Nova/RAG sees; the rich `view` payload is unchanged).

    Principle: store information the way it would be asked about. Keep each
    row's label glued to its value(s) as ONE coherent line and drop the empty
    padding cells pdfplumber inserts — so "Annotated images: 838" embeds and
    retrieves as a unit instead of being flattened into "| | 838 |" pipe-soup
    or split across chunks.
    """
    clean: list[list[str]] = []
    for r in (rows or []):
        cells = [("" if c is None else str(c).strip()) for c in (r or [])]
        if any(cells):
            clean.append(cells)
    if not clean:
        return []

    def ne(row: list[str]) -> list[str]:
        return [c for c in row if c]

    # Key/value table (every row ≤2 non-empty cells) → "label: value".
    if all(len(ne(r)) <= 2 for r in clean):
        out: list[str] = []
        for r in clean:
            v = ne(r)
            if len(v) == 2:
                out.append(f"  {v[0]}: {v[1]}")
            elif len(v) == 1:
                out.append(f"  {v[0]}")
        return out

    # Wider table → prefix each cell with its column header so every row is
    # self-describing ("Step: Convert; Output: manifest"), header kept too.
    header = clean[0]
    out = ["  " + " | ".join(ne(header))]
    for r in clean[1:]:
        pairs: list[str] = []
        for i, cell in enumerate(r):
            if not cell:
                continue
            col = header[i] if i < len(header) and header[i] else ""
            pairs.append(f"{col}: {cell}" if col else cell)
        if pairs:
            out.append("  " + "; ".join(pairs))
    return out


# ---------------------------------------------------------------- PDF ---

def _extract_pdf(blob: bytes) -> ExtractResult:
    """pdfplumber for layout-aware text + tables, pypdf as fallback.
    View payload is a per-page list so the tile can render pages discretely."""
    text_parts: list[str] = []
    pages: list[dict[str, Any]] = []
    used_plumber = False
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(io.BytesIO(blob)) as pdf:
            for i, page in enumerate(pdf.pages[:PAGE_CAP], start=1):
                page_lines: list[str] = []
                txt = (page.extract_text() or "").strip()
                if txt:
                    page_lines.append(txt)
                for ti, table in enumerate(page.extract_tables() or [], start=1):
                    if not table: continue
                    page_lines.append(f"[table {ti}]")
                    page_lines.extend(_render_table_rows(table))
                page_text = "\n".join(page_lines)
                pages.append({"n": i, "text": page_text})
                text_parts.append(f"--- page {i} ---")
                if page_text: text_parts.append(page_text)
        used_plumber = True
    except Exception as e:
        log.warning("pdfplumber failed; falling back to pypdf: %s", e)
        pages = []
        text_parts = []

    if not used_plumber:
        try:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(io.BytesIO(blob))
            for i, page in enumerate(reader.pages[:PAGE_CAP], start=1):
                txt = (page.extract_text() or "").strip()
                pages.append({"n": i, "text": txt})
                text_parts.append(f"--- page {i} ---")
                if txt: text_parts.append(txt)
        except Exception as e:
            log.warning("pypdf failed too: %s", e)
            return "", None

    text = "\n".join(text_parts).strip()
    view = {"type": "pages", "pages": pages} if pages else None
    return text, view


# ---------------------------------------------------------------- .docx ---

def _extract_docx(blob: bytes) -> ExtractResult:
    """python-docx — paragraphs (with heading style hints) + tables.
    View payload is a list of blocks the tile renders as flowing prose."""
    try:
        from docx import Document
    except Exception as e:
        log.warning("python-docx not importable: %s", e)
        return "", None
    try:
        doc = Document(io.BytesIO(blob))
    except Exception as e:
        log.warning("docx open failed: %s", e)
        return "", None

    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text: continue
        style = (para.style.name if para.style else "") or ""
        # Heading 1..6 from Word maps to {h1, h2, ...} for the tile renderer.
        m = re.match(r"Heading\s+(\d)", style)
        if m:
            level = min(6, max(1, int(m.group(1))))
            blocks.append({"type": "heading", "level": level, "text": text})
        elif style.lower().startswith("title"):
            blocks.append({"type": "heading", "level": 1, "text": text})
        else:
            blocks.append({"type": "p", "text": text})
        text_parts.append(text)
    for ti, table in enumerate(doc.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            rows.append(cells)
        if rows:
            blocks.append({"type": "table", "rows": rows[:TABLE_ROW_CAP]})
            text_parts.append(f"\n[table {ti}]")
            text_parts.extend(_render_table_rows(rows))

    text = "\n".join(text_parts).strip()
    view = {"type": "doc", "blocks": blocks} if blocks else None
    return text, view


# ---------------------------------------------------------------- .xlsx ---

def _extract_xlsx(blob: bytes) -> ExtractResult:
    """openpyxl — every sheet as a 2D rows array.
    View payload is sheet-by-sheet; tile renders <table>."""
    try:
        from openpyxl import load_workbook
    except Exception as e:
        log.warning("openpyxl not importable: %s", e)
        return "", None
    try:
        wb = load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
    except Exception as e:
        log.warning("xlsx open failed: %s", e)
        return "", None

    sheets: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows: list[list[Any]] = []
        for row in ws.iter_rows(values_only=True):
            row_list = list(row)
            while row_list and (row_list[-1] is None or row_list[-1] == ""):
                row_list.pop()
            if not row_list: continue
            rows.append(["" if c is None else (c if isinstance(c, (int, float, bool)) else str(c)) for c in row_list])
            if len(rows) >= TABLE_ROW_CAP:
                rows.append(["…", f"(+{ws.max_row - TABLE_ROW_CAP} more rows truncated)"])
                break
        sheets.append({"name": sheet_name, "rows": rows})
        text_parts.append(f"--- sheet: {sheet_name} ---")
        text_parts.extend(_render_table_rows(rows))

    text = "\n".join(text_parts).strip()
    view = {"type": "table", "sheets": sheets} if sheets else None
    return text, view


# ---------------------------------------------------------------- .pptx ---

def _extract_pptx(blob: bytes) -> ExtractResult:
    """python-pptx — slide text + speaker notes; tile renders slide cards."""
    try:
        from pptx import Presentation
    except Exception as e:
        log.warning("python-pptx not importable: %s", e)
        return "", None
    try:
        prs = Presentation(io.BytesIO(blob))
    except Exception as e:
        log.warning("pptx open failed: %s", e)
        return "", None

    slides: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        if i > SLIDE_CAP: break
        title = ""
        lines: list[str] = []
        tables: list[list[list[str]]] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    txt = "".join(run.text for run in para.runs).strip()
                    if not txt: continue
                    if not title:
                        title = txt
                    else:
                        lines.append(txt)
            if getattr(shape, "has_table", False):
                tbl_rows = []
                for row in shape.table.rows:
                    tbl_rows.append([c.text.strip().replace("\n", " ") for c in row.cells])
                if tbl_rows: tables.append(tbl_rows)
        notes = ""
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        slides.append({"n": i, "title": title, "lines": lines, "tables": tables, "notes": notes})

        text_parts.append(f"--- slide {i} ---")
        if title: text_parts.append(title)
        text_parts.extend(lines)
        for tbl_rows in tables:
            text_parts.append("[table]")
            text_parts.extend(_render_table_rows(tbl_rows))
        if notes:
            text_parts.append("[notes]")
            text_parts.append(notes)

    text = "\n".join(text_parts).strip()
    view = {"type": "slides", "slides": slides} if slides else None
    return text, view


# ---------------------------------------------------------------- dispatch ---

_EXTRACTORS: dict[str, Callable[[bytes], ExtractResult]] = {
    "pdf":  _extract_pdf,
    "docx": _extract_docx,
    "xlsx": _extract_xlsx,
    "xlsm": _extract_xlsx,
    "pptx": _extract_pptx,
}


router = APIRouter(prefix="/files", tags=["files"])


@router.post("/extract")
async def extract(file: UploadFile = File(...)) -> dict:
    name = file.filename or "upload"
    ext = _file_ext(name)
    if ext not in _EXTRACTORS:
        return {"kind": "binary", "text": "", "name": name, "size": 0,
                "ext": ext, "view": None, "reason": f"unsupported extension '.{ext}'"}

    blob = await file.read()
    size = len(blob)
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
            detail=f"file too large ({size} bytes; cap is {MAX_UPLOAD_BYTES})")
    if size == 0:
        return {"kind": "binary", "text": "", "name": name, "size": 0,
                "ext": ext, "view": None, "reason": "empty upload"}

    try:
        text, view = _EXTRACTORS[ext](blob)
    except Exception as e:
        log.exception("extractor crashed for .%s: %s", ext, e)
        return {"kind": "binary", "text": "", "name": name, "size": size,
                "ext": ext, "view": None, "reason": f"extractor crashed: {e}"}

    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text and not view:
        return {"kind": "binary", "text": "", "name": name, "size": size,
                "ext": ext, "view": None, "reason": "no text recovered"}

    return {"kind": "text", "text": text, "name": name, "size": size, "ext": ext, "view": view}
