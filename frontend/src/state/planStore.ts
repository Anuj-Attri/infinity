// Per-instance state for Plan tiles (multi-instance — each /plan keeps its own
// tile, slugged /plan-<name>). The recipe (App.runPlan) writes the live stage +
// streamed markdown here; PlanTile reads it by its tileId. Module store +
// listeners, keyed by tileId (mirrors the ArtifactTile pattern).

export type PlanStage = "researching" | "scoping" | "diagramming" | "synthesizing" | "done" | "error";

export type PlanState = {
  goal: string;
  slug: string;
  stage: PlanStage;
  text: string;     // streamed plan markdown ("" until synthesis starts)
  note: string;     // e.g. "diagram skipped" — honest skip notes
  error: string;    // "" unless a critical step failed
};

const _state = new Map<string, PlanState>();
const _subs = new Map<string, Set<() => void>>();

function notify(id: string) { _subs.get(id)?.forEach((cb) => { try { cb(); } catch { /* ignore */ } }); }

export function getPlan(id: string): PlanState | undefined { return _state.get(id); }
export function subscribePlan(id: string, cb: () => void): () => void {
  if (!_subs.has(id)) _subs.set(id, new Set());
  _subs.get(id)!.add(cb);
  return () => _subs.get(id)?.delete(cb);
}

function patch(id: string, p: Partial<PlanState>) {
  const cur = _state.get(id);
  if (!cur) return;
  _state.set(id, { ...cur, ...p });
  notify(id);
}

export function planStart(id: string, goal: string, slug: string): void {
  _state.set(id, { goal, slug, stage: "researching", text: "", note: "", error: "" });
  notify(id);
}
export function planStage(id: string, stage: PlanStage): void { patch(id, { stage }); }
export function planNote(id: string, note: string): void { patch(id, { note }); }
export function planText(id: string, text: string): void { patch(id, { text, stage: "synthesizing" }); }
export function planDone(id: string, text: string): void { patch(id, { text, stage: "done" }); }
export function planError(id: string, error: string): void { patch(id, { stage: "error", error }); }
