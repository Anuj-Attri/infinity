// Docs tile — document Q&A + indexed-doc resting state.
//
// Two states, driven by docsStore's cached view:
//   • "query"   — the latest query_documents run: the question, Nova's grounded
//     answer (once it completes), and the citations (source + chunk + score).
//   • "indexed" — the resting state shown the moment a file finishes indexing:
//     the indexed doc(s) + a grounded VERBATIM excerpt of the most-recent one,
//     so the user sees the doc is ready before asking anything.
// Read-only surface; populated by App's runDocQuery (query) and FileTile's
// index action (indexed). Standard movable/resizable/closable tile.

import { useEffect, useState, useSyncExternalStore } from "react";
import { getDocsView, subscribeDocs, setDocsIndexed, clearDocsView } from "../../state/docsStore";
import { getRagDocs, ragPreview, clearRagDocs } from "../../api";
import TileGlyph from "../TileGlyph";

function snippet(s: string, n = 240): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n) + "…" : t;
}

export default function DocsTile() {
  const view = useSyncExternalStore(subscribeDocs, getDocsView);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  // Clear ALL indexed documents (doc_chunks only — never Nova's memory). On
  // success, drop the local roster to the placeholder and emit the shared
  // index-changed signal so the Vision graph + any Docs view refresh live.
  const clearAll = async () => {
    setBusy(true);
    try {
      await clearRagDocs();
      clearDocsView();
    } catch { /* leave the view; fail quietly per the rest of the tile */ }
    finally { setBusy(false); setConfirming(false); }
  };
  const stop = (e: React.MouseEvent) => e.stopPropagation();

  // On spawn, reflect whatever is ALREADY indexed in the RAG store (including
  // docs from prior sessions) — show the "indexed and ready" resting state
  // instead of the empty placeholder. Only when there's no current view (so an
  // in-session query/index result is never clobbered). Fail-closed: any error →
  // the placeholder, no crash. (Read-only — doesn't touch RAG ingest/retrieval.)
  useEffect(() => {
    if (getDocsView()) return;
    let alive = true;
    (async () => {
      const { docs } = await getRagDocs();
      if (!alive || !docs.length || getDocsView()) return;
      const sources = [...new Set(docs.map((d) => d.source))];
      const latest = docs[0].source;            // list_docs() is newest-first
      let chunks = docs[0].chunks;
      let excerpt = "";
      try { const pv = await ragPreview(latest); excerpt = pv.excerpt; chunks = pv.chunks || chunks; }
      catch { /* keep the count; excerpt optional */ }
      if (!alive || getDocsView()) return;
      setDocsIndexed({ sources, latest, chunks, excerpt });
    })();
    return () => { alive = false; };
  }, []);

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Docs</span>
        <span className="tile-title-meta tdocs-head-right">
          <span className="tdocs-count">
            {view?.mode === "query"
              ? `${view.data.chunks.length} cited`
              : view?.mode === "indexed"
                ? `${view.data.sources.length} indexed`
                : ""}
          </span>
          {view && (confirming ? (
            <span className="tdocs-clear-confirm" onMouseDown={stop} onClick={stop}>
              <span className="tdocs-clear-ask">Clear all docs?</span>
              <button type="button" className="tdocs-clear-yes" onClick={clearAll} disabled={busy}>Clear</button>
              <button type="button" className="tdocs-clear-no" onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
            </span>
          ) : (
            <button
              type="button"
              className="tdocs-clear"
              onMouseDown={stop}
              onClick={(e) => { stop(e); setConfirming(true); }}
            >
              Clear all
            </button>
          ))}
        </span>
      </div>
      <div
        className="tile-body tdocs-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {!view ? (
          <div className="tdocs-empty">Ask about an indexed file</div>
        ) : view.mode === "indexed" ? (
          // Resting state — a doc was indexed; reflect it (grounded, no Q yet).
          <>
            <div className="tdocs-indexed-h">Indexed and ready</div>
            <div className="tdocs-indexed-doc">
              {view.data.latest}
              <span className="tdocs-indexed-meta"> · {view.data.chunks} chunk{view.data.chunks === 1 ? "" : "s"}</span>
            </div>
            {view.data.excerpt
              ? <div className="tdocs-indexed-excerpt">{snippet(view.data.excerpt, 360)}</div>
              : <div className="tdocs-nocite">Indexed — ask me about it.</div>}
            {view.data.sources.length > 1 && (
              <div className="tdocs-indexed-set">
                <div className="tdocs-cites-h">All indexed</div>
                {view.data.sources.map((s) => (
                  <div key={s} className={`tdocs-indexed-src ${s === view.data.latest ? "latest" : ""}`}>{s}</div>
                ))}
              </div>
            )}
            <div className="tdocs-indexed-hint">Ask a question to get a cited answer.</div>
          </>
        ) : (
          // Query state — the Q&A path (unchanged).
          <>
            <div className="tdocs-q">{view.data.question}</div>
            {view.data.answer
              ? <div className="tdocs-answer">{view.data.answer}</div>
              : <div className="tdocs-answer muted">…</div>}
            {view.data.chunks.length === 0 ? (
              <div className="tdocs-nocite">No matching passages in your documents.</div>
            ) : (
              <div className="tdocs-cites">
                <div className="tdocs-cites-h">Sources</div>
                {view.data.chunks.map((c, i) => (
                  <div className="tdocs-cite" key={i}>
                    <div className="tdocs-cite-src">
                      [{c.source}] <span className="tdocs-cite-meta">#{c.chunk_index} · {(c.score * 100).toFixed(0)}%</span>
                    </div>
                    <div className="tdocs-cite-text">{snippet(c.text)}</div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
