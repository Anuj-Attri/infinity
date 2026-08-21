// Chain progress + gate state for the Chain tile. Holds the live step list
// (status per step), the active index, and — when a CRITICAL step fails — a
// gate prompt the user answers to resume/redirect the chain. Module store +
// listeners (mirrors summaryStore/researchStore). The gate uses a Promise:
// runChain awaits openGate(); the Chain tile's buttons call resolveGate().

export type ChainStepStatus = "pending" | "running" | "done" | "skipped" | "failed";

export type ChainStepView = {
  label: string;       // e.g. "Research"
  cmd: string;
  status: ChainStepStatus;
  note?: string;       // skip/fail reason (clean, user-facing)
  stage?: string;      // live sub-stage of the running step (e.g. "searching…")
};

export type GateAction = "retry" | "skip" | "abort";
export type GateChoice = { action: GateAction; guidance?: string };

export type ChainGate = { stepIdx: number; label: string; reason: string };

export type ChainState = {
  steps: ChainStepView[];
  active: number;          // index of the running step (-1 when idle/done)
  gate: ChainGate | null;  // non-null while awaiting the user's decision
  status: "running" | "done" | "aborted";
  summary: string;         // honest final line (e.g. "2 done, 1 skipped")
  ts: number;
};

let _state: ChainState | null = null;
let _gateResolver: ((c: GateChoice) => void) | null = null;
const subs = new Set<() => void>();

function notify() { subs.forEach((cb) => { try { cb(); } catch { /* ignore */ } }); }
function patch(p: Partial<ChainState>) { if (_state) { _state = { ..._state, ...p }; notify(); } }

export function getChain(): ChainState | null { return _state; }
export function subscribeChain(cb: () => void): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}

export function chainStart(steps: ChainStepView[]): void {
  _state = { steps, active: -1, gate: null, status: "running", summary: "", ts: Date.now() };
  notify();
}

function updateStep(idx: number, p: Partial<ChainStepView>): void {
  if (!_state) return;
  const steps = _state.steps.map((s, i) => (i === idx ? { ...s, ...p } : s));
  _state = { ..._state, steps };
  notify();
}

export function chainSetActive(idx: number): void { patch({ active: idx }); updateStep(idx, { status: "running" }); }
export function chainStepStage(idx: number, stage: string): void { updateStep(idx, { stage }); }
export function chainStepDone(idx: number): void { updateStep(idx, { status: "done", stage: undefined }); }
export function chainStepSkipped(idx: number, note: string): void { updateStep(idx, { status: "skipped", note, stage: undefined }); }
export function chainStepFailed(idx: number, note: string): void { updateStep(idx, { status: "failed", note, stage: undefined }); }

export function chainFinish(summary: string): void { patch({ active: -1, status: "done", summary, gate: null }); }
export function chainAbort(summary: string): void { patch({ active: -1, status: "aborted", summary, gate: null }); }

/** Open a gate and AWAIT the user's choice (resolved by the Chain tile). */
export function openGate(gate: ChainGate): Promise<GateChoice> {
  return new Promise<GateChoice>((resolve) => {
    _gateResolver = resolve;
    patch({ gate });
  });
}

/** Called by the Chain tile's buttons. Clears the gate and resumes runChain. */
export function resolveGate(choice: GateChoice): void {
  const r = _gateResolver;
  _gateResolver = null;
  patch({ gate: null });
  if (r) r(choice);
}
