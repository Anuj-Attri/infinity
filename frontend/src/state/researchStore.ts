// Shared state for the ephemeral Research tile. Holds ONLY the latest
// /research run — a new run replaces it; nothing persisted. Mirrors summaryStore
// but adds a `stage` so the tile can show a progress bar across the multi-step
// flow (searching → assessing → streaming the brief).

export type ResearchStage = "searching" | "assessing" | "streaming" | "done" | "error";

export type ResearchResult = {
  query: string;          // topic or referenced source label
  stage: ResearchStage;
  text: string;           // streamed assessment ("" until it arrives)
  error: string;          // "" unless failed
  ts: number;
};

let _state: ResearchResult | null = null;
const subs = new Set<() => void>();

function notify() { subs.forEach((cb) => { try { cb(); } catch { /* ignore */ } }); }

export function getResearch(): ResearchResult | null { return _state; }

export function startResearch(query: string): void {
  _state = { query, stage: "searching", text: "", error: "", ts: Date.now() };
  notify();
}
export function setResearchStage(stage: ResearchStage): void {
  if (_state) { _state = { ..._state, stage }; notify(); }
}
export function setResearchText(text: string): void {
  if (_state) { _state = { ..._state, text, stage: "streaming" }; notify(); }
}
export function finishResearch(): void {
  if (_state) { _state = { ..._state, stage: "done" }; notify(); }
}
export function setResearchError(error: string): void {
  if (_state) { _state = { ..._state, stage: "error", text: "", error }; notify(); }
}

export function subscribeResearch(cb: () => void): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}
