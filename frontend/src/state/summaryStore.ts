// Shared state for the ephemeral Summary tile. Holds ONLY the latest
// /summarize output — a new summarize replaces it; nothing is persisted (no
// history, survives no reload). Module store + listeners, mirroring docsStore.

export type SummaryResult = {
  sources: string[];   // tile refs that were summarized (e.g. ["notes","news"])
  focus: string;       // focus instruction, or "" for a general summary
  summary: string;     // "" while generating
  loading: boolean;
  error: string;       // "" unless the summarize failed / had no content
  ts: number;
};

let _state: SummaryResult | null = null;
const subs = new Set<() => void>();

function notify() { subs.forEach((cb) => { try { cb(); } catch { /* ignore */ } }); }

export function getSummary(): SummaryResult | null { return _state; }

/** Begin a new summarize — replaces any prior result (no history). */
export function startSummary(sources: string[], focus: string): void {
  _state = { sources, focus, summary: "", loading: true, error: "", ts: Date.now() };
  notify();
}
export function setSummaryText(summary: string): void {
  if (_state) { _state = { ..._state, summary, loading: false, error: "" }; notify(); }
}
export function setSummaryError(error: string): void {
  if (_state) { _state = { ..._state, summary: "", loading: false, error }; notify(); }
}

export function subscribeSummary(cb: () => void): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}
