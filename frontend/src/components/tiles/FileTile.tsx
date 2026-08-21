// Attached-file tile.
//
// Rendering layers (in order of preference):
//   1. file.view present  -> rich rendering by type:
//        - "table"  -> per-sheet <table> with headers
//        - "slides" -> per-slide cards
//        - "pages"  -> per-page sections
//        - "doc"    -> headings + paragraphs + inline tables (.docx)
//   2. file.text   present -> <pre> dump
//   3. file.dataUrl image  -> <img>
//   4. else                -> binary card
//
// The plaintext (file.text) is what Nova receives via /slug context
// injection. This component is purely the human-facing surface.

import { useEffect, useMemo, useState } from "react";
import type { FileItem, FileView } from "../../state/FilesContext";
import { vaultIngest, vaultIngestFile } from "../../api";
import { useTiles } from "../../state/TilesContext";
import TileGlyph from "../TileGlyph";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function isPdf(file: FileItem): boolean {
  return /\.pdf$/i.test(file.name) || file.mime === "application/pdf";
}

// Explicit ingest action: file the document into the VAULT (the one unified
// knowledge base). Prefers the real BYTES (server re-extracts + OCRs scanned
// PDFs) over any pre-inlined text, and reports HONEST state — "Filed ✓" only
// when a note was actually written; "No readable text" when extraction + OCR
// recovered nothing (nothing is stored). Never claims a false index.
function IndexButton({ file }: { file: FileItem }) {
  const [state, setState] = useState<"idle" | "working" | "done" | "notext" | "err">("idle");
  const { spawnTile } = useTiles();
  const canIndex = !!file.blob || (file.kind === "text" && !!file.text);
  if (!canIndex) return null;
  const label =
    state === "working" ? "Filing…" :
    state === "done"    ? "In Vault ✓" :
    state === "notext"  ? "No readable text" :
    state === "err"     ? "Retry" : "Add to Vault";
  return (
    <button
      type="button"
      className={`tfile-index no-drag ${state === "done" ? "done" : ""} ${state === "notext" || state === "err" ? "err" : ""}`}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={async (e) => {
        e.stopPropagation();
        if (state === "working") return;
        setState("working");
        try {
          const r = file.blob
            ? await vaultIngestFile(file.blob, file.name)             // real bytes → server extracts + OCR
            : await vaultIngest({ text: file.text || "", title: file.name }); // plain-text path
          setState(r.node ? "done" : "notext");
          // Open the Vault so the new note is visible in tree + graph + search,
          // and signal the live index so an open Vision tile re-fetches.
          try { spawnTile("vault"); } catch { /* ignore */ }
        } catch (e2) {
          const msg = e2 instanceof Error ? e2.message : "";
          // 422 "no text in document..." => loud, specific failure; nothing stored.
          setState(/no text|no readable/i.test(msg) ? "notext" : "err");
        }
      }}
      title="File this document into the Vault"
    >
      {label}
    </button>
  );
}

// Real PDF viewer — renders the actual document from its kept bytes.
function PdfView({ file }: { file: FileItem }) {
  const url = useMemo(() => (file.blob ? URL.createObjectURL(file.blob) : ""), [file.blob]);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  return (
    <div className="tile">
      {tileHead(file, "PDF")}
      <div className="tile-body tfile-body tfile-pdf-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
        {url
          ? <iframe className="tfile-pdf-frame" src={url} title={file.name} />
          : <div className="tfile-binary-note">PDF preview unavailable.</div>}
      </div>
      <Foot file={file} />
    </div>
  );
}

function tileHead(file: FileItem, kindLabel: string) {
  return (
    <div className="tile-head">
      <span className="tile-title">
        <TileGlyph /> {kindLabel}
      </span>
      <span className="tile-title-meta">/{file.slug} · {fmtBytes(file.size)}</span>
    </div>
  );
}

function Foot({ file, sub }: { file: FileItem; sub?: string }) {
  return (
    <div className={`tfile-foot ${sub ? "tfile-foot-note" : ""}`}>
      <span className="tfile-foot-name">{file.name}</span>
      {sub && <span className="tfile-foot-sub">{sub}</span>}
      <IndexButton file={file} />
    </div>
  );
}

// ── Rich views ──────────────────────────────────────────────────

function TableView({ view }: { view: Extract<FileView, { type: "table" }> }) {
  return (
    <div className="tfile-rich tfile-rich-table">
      {view.sheets.map((sh, si) => (
        <section key={si} className="tfile-sheet">
          <div className="tfile-section-h">{sh.name || `Sheet ${si + 1}`}</div>
          <div className="tfile-table-scroll">
            <table className="tfile-table">
              <tbody>
                {sh.rows.map((row, ri) => {
                  const Cell = ri === 0 ? "th" : "td";
                  return (
                    <tr key={ri}>
                      {row.map((c, ci) => <Cell key={ci}>{String(c)}</Cell>)}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

function SlidesView({ view }: { view: Extract<FileView, { type: "slides" }> }) {
  return (
    <div className="tfile-rich tfile-rich-slides">
      {view.slides.map((s) => (
        <article key={s.n} className="tfile-slide">
          <header className="tfile-slide-h">
            <span className="tfile-slide-n">Slide {s.n}</span>
            {s.title && <span className="tfile-slide-t">{s.title}</span>}
          </header>
          <div className="tfile-slide-body">
            {(s.lines || []).map((line, i) => (
              <p key={i} className="tfile-slide-line">{line}</p>
            ))}
            {(s.tables || []).map((tbl, ti) => (
              <table key={ti} className="tfile-table tfile-slide-table">
                <tbody>
                  {tbl.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((c, ci) => {
                        const Cell = ri === 0 ? "th" : "td";
                        return <Cell key={ci}>{c}</Cell>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
          </div>
          {s.notes && (
            <footer className="tfile-slide-notes">
              <span className="tfile-slide-notes-label">Notes</span>
              <span className="tfile-slide-notes-body">{s.notes}</span>
            </footer>
          )}
        </article>
      ))}
    </div>
  );
}

function PagesView({ view }: { view: Extract<FileView, { type: "pages" }> }) {
  return (
    <div className="tfile-rich tfile-rich-pages">
      {view.pages.map((p) => (
        <section key={p.n} className="tfile-page">
          <header className="tfile-page-h">Page {p.n}</header>
          <pre className="tfile-page-body">{p.text}</pre>
        </section>
      ))}
    </div>
  );
}

function DocView({ view }: { view: Extract<FileView, { type: "doc" }> }) {
  return (
    <div className="tfile-rich tfile-rich-doc">
      {view.blocks.map((b, i) => {
        if (b.type === "heading") {
          const lv = Math.min(6, Math.max(1, b.level));
          const cls = `tfile-h tfile-h${lv}`;
          switch (lv) {
            case 1: return <h1 key={i} className={cls}>{b.text}</h1>;
            case 2: return <h2 key={i} className={cls}>{b.text}</h2>;
            case 3: return <h3 key={i} className={cls}>{b.text}</h3>;
            case 4: return <h4 key={i} className={cls}>{b.text}</h4>;
            case 5: return <h5 key={i} className={cls}>{b.text}</h5>;
            default: return <h6 key={i} className={cls}>{b.text}</h6>;
          }
        }
        if (b.type === "p") {
          return <p key={i} className="tfile-p">{b.text}</p>;
        }
        // table
        return (
          <table key={i} className="tfile-table tfile-doc-table">
            <tbody>
              {b.rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((c, ci) => {
                    const Cell = ri === 0 ? "th" : "td";
                    return <Cell key={ci}>{c}</Cell>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        );
      })}
    </div>
  );
}

// ── Top-level dispatch ──────────────────────────────────────────

export default function FileTile({ file }: { file: FileItem }) {
  // Real PDF viewer takes precedence over the text dump so the user can read
  // the actual document; the kept bytes also power RAG ingest.
  if (isPdf(file) && file.blob) {
    return <PdfView file={file} />;
  }

  if (file.kind === "image" && file.dataUrl) {
    return (
      <div className="tile">
        {tileHead(file, "IMAGE")}
        <div className="tile-body tfile-body tfile-image-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
          <img src={file.dataUrl} alt={file.name} className="tfile-image" draggable={false} />
        </div>
        <Foot file={file} sub="Local · Nova can't see image contents yet (text-only model)." />
      </div>
    );
  }

  if (file.kind === "text" && file.view) {
    const v = file.view;
    return (
      <div className="tile">
        {tileHead(file, v.type === "table" ? "SPREADSHEET" : v.type === "slides" ? "DECK" : v.type === "pages" ? "PDF" : "DOC")}
        <div className="tile-body tfile-body tfile-rich-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
          {v.type === "table"  && <TableView  view={v} />}
          {v.type === "slides" && <SlidesView view={v} />}
          {v.type === "pages"  && <PagesView  view={v} />}
          {v.type === "doc"    && <DocView    view={v} />}
        </div>
        <Foot file={file} />
      </div>
    );
  }

  if (file.kind === "text" && file.text != null) {
    return (
      <div className="tile">
        {tileHead(file, "DOC")}
        <div className="tile-body tfile-body tfile-text-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
          <pre className="tfile-text">{file.text}</pre>
        </div>
        <Foot file={file} />
      </div>
    );
  }

  // Binary fallback.
  return (
    <div className="tile">
      {tileHead(file, "FILE")}
      <div className="tile-body tfile-body tfile-binary-body">
        <div className="tfile-binary">
          <div className="tfile-binary-name">{file.name}</div>
          <div className="tfile-binary-sub">{file.mime}</div>
          <div className="tfile-binary-note">
            Binary content not displayed. The filename is available to Nova via /{file.slug}.
          </div>
        </div>
      </div>
      {file.blob && <Foot file={file} />}
    </div>
  );
}
