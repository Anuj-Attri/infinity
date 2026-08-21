// Shared state for the cited "Docs" tile. Two slots, one tile:
//
//   • QUERY  — a query_documents run publishes the question + retrieved
//     citations here; the answer fills in once Nova's grounded reply completes.
//   • INDEXED — the resting state: when a file finishes indexing, the Docs tile
//     proactively reflects the indexed doc(s) + a grounded verbatim excerpt, so
//     the user sees the doc is ready WITHOUT having to ask first.
//
// The tile shows whichever slot is more recent: ask a question → Q&A wins;
// index a new doc → the indexed resting state wins. The view object is cached
// (recomputed only on change) so useSyncExternalStore doesn't loop.
//
// Module store + listeners (no provider needed), mirroring the tile-write bus.

import type { DocChunk } from "../api";

export type DocsResult = {
  question: string;
  chunks: DocChunk[];
  answer: string;       // filled when the grounded chat reply completes
  ts: number;
};

export type DocsIndexed = {
  sources: string[];    // all indexed source names (the set)
  latest: string;       // most-recently indexed source (featured)
  chunks: number;       // chunk count of `latest`
  excerpt: string;      // VERBATIM leading text of `latest` (grounded, may be "")
  ts: number;
};

export type DocsView =
  | { mode: "query";   data: DocsResult }
  | { mode: "indexed"; data: DocsIndexed }
  | null;

let _query: DocsResult | null = null;
let _indexed: DocsIndexed | null = null;
let _view: DocsView = null;
const subs = new Set<() => void>();

function recompute(): void {
  if (_query && (!_indexed || _query.ts >= _indexed.ts)) _view = { mode: "query", data: _query };
  else if (_indexed) _view = { mode: "indexed", data: _indexed };
  else _view = null;
}

function notify() { subs.forEach((cb) => { try { cb(); } catch { /* ignore */ } }); }

/** Cached, stable snapshot for useSyncExternalStore (new ref only on change). */
export function getDocsView(): DocsView { return _view; }

// ── Query slot (query_documents — the Q&A path; unchanged contract) ──
export function setDocsQuery(question: string, chunks: DocChunk[]): void {
  _query = { question, chunks, answer: "", ts: Date.now() };
  recompute();
  notify();
}

export function setDocsAnswer(answer: string): void {
  if (_query) { _query = { ..._query, answer }; recompute(); notify(); }
}

// ── Indexed slot (populate-on-index resting state) ──
export function setDocsIndexed(info: Omit<DocsIndexed, "ts">): void {
  _indexed = { ...info, ts: Date.now() };
  recompute();
  notify();
}

// ── Clear (clear-all-docs) — drop both slots back to the empty placeholder. ──
export function clearDocsView(): void {
  _query = null;
  _indexed = null;
  recompute();
  notify();
}

export function subscribeDocs(cb: () => void): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}
