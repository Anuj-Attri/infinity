"""OCR — read text out of scanned PDFs and images via the vision lane.

Classic OCR needs a tesseract binary + poppler; instead this reuses the
dispatch (OpenRouter) vision model already wired for the Eye and Vault
captions. It renders PDF pages to images (PyMuPDF, the same lib the PDF
extractor uses; pypdfium2 fallback) and asks the VLM to transcribe. Higher
accuracy on messy scans than tesseract, no system binaries.

Used as the FALLBACK in Vault ingestion: when normal text extraction yields
nothing (a scanned/image-only PDF — the "No readable text" case), OCR kicks
in so the document still becomes a searchable note. Fail-closed: no vision
provider or no renderer → returns "" (caller reports honestly).
"""

from __future__ import annotations

import base64
import io
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/ocr", tags=["ocr"])

_MAX_PAGES = 15            # cost/latency bound per document (MVP)
_RENDER_SCALE = 2.0       # ~150–200 DPI; enough for the VLM to read fine print
_OCR_PROMPT = (
    "Transcribe ALL text in this image exactly, as OCR. Preserve reading order "
    "and structure (headings, code blocks, tables as best you can). Output ONLY "
    "the transcribed text — no commentary. If the image contains no readable "
    "text, output nothing."
)


def available() -> bool:
    import importlib.util
    has_render = (importlib.util.find_spec("fitz") is not None
                  or importlib.util.find_spec("pypdfium2") is not None)
    return has_render


async def _vlm_transcribe(png: bytes) -> str:
    from .providers import dispatch
    provider = dispatch.get_provider()
    if provider is None:
        return ""
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": _OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]
    model = dispatch.resolve_model("smart")
    try:
        text = await provider.complete(messages, model=model, temperature=0.0, max_tokens=2000)
        return (text or "").strip()
    except Exception as e:
        print(f"[ocr] vlm transcribe failed (non-fatal): {type(e).__name__}", flush=True)
        return ""


def _render_pdf_pages(blob: bytes, max_pages: int) -> list[bytes]:
    """PDF bytes → list of PNG page images. PyMuPDF first, pypdfium2 fallback."""
    # PyMuPDF (fitz)
    try:
        import fitz  # type: ignore
        pages: list[bytes] = []
        doc = fitz.open(stream=blob, filetype="pdf")
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            mat = fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            pages.append(pix.tobytes("png"))
        doc.close()
        return pages
    except Exception:
        pass
    # pypdfium2
    try:
        import pypdfium2 as pdfium  # type: ignore
        pages = []
        doc = pdfium.PdfDocument(blob)
        n = min(len(doc), max_pages)
        for i in range(n):
            bitmap = doc[i].render(scale=_RENDER_SCALE)
            pil = bitmap.to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            pages.append(buf.getvalue())
        return pages
    except Exception as e:
        print(f"[ocr] pdf render failed: {type(e).__name__}: {e}", flush=True)
        return []


async def ocr_pdf(blob: bytes, max_pages: int = _MAX_PAGES) -> str:
    """Scanned/image PDF → transcribed text (page markers between pages)."""
    pages = _render_pdf_pages(blob, max_pages)
    if not pages:
        return ""
    out: list[str] = []
    for i, png in enumerate(pages):
        txt = await _vlm_transcribe(png)
        if txt:
            out.append(f"--- page {i + 1} ---\n{txt}")
    return "\n\n".join(out).strip()


async def ocr_image(blob: bytes) -> str:
    """Single image → transcribed text (empty if none)."""
    return await _vlm_transcribe(blob)


# ── endpoint (also usable directly) ──────────────────────────────────────────

class OcrResult(BaseModel):
    ok: bool
    text: str
    chars: int


@router.post("/file")
async def ocr_file(file: UploadFile = File(...)) -> OcrResult:
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=422, detail="empty file")
    name = (file.filename or "").lower()
    if name.endswith(".pdf"):
        text = await ocr_pdf(blob)
    else:
        text = await ocr_image(blob)
    return OcrResult(ok=bool(text), text=text, chars=len(text))
