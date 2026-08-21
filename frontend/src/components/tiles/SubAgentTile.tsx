// SubAgent tile — streams an OpenRouter sub-agent response.
// Nova spawns this tile when a dispatch_subagent tool call is approved.
//
// DISPATCH LIFECYCLE: Independent of React. Jobs are registered + dispatched
// from executeToolCall in App.tsx (module-level, before the tile mounts).
// The component subscribes to output updates via a listener map.
// React StrictMode unmount/remount does NOT abort the in-flight request.

import { useEffect, useState } from "react";
import { postDispatchSubagent } from "../../api";
import { useTileId } from "../../state/TilesContext";
import MarkdownView from "../MarkdownView";

export type SubAgentJob = {
  role: string;
  task: string;
  modelTier: "fast" | "smart";
};

type RunState = {
  output: string;
  status: "running" | "done" | "error";
  errorMsg: string | null;
};

// ── Module-level state — survives React StrictMode remounts ──────────────────
const _JOBS    = new Map<string, SubAgentJob>();
const _STATE   = new Map<string, RunState>();
const _STARTED = new Set<string>();
const _SUBS    = new Map<string, Set<() => void>>();

export function registerSubAgentJob(tileId: string, job: SubAgentJob): void {
  _JOBS.set(tileId, job);
}

export function getSubAgentJob(tileId: string): SubAgentJob | undefined {
  return _JOBS.get(tileId);
}

function notify(tileId: string): void {
  _SUBS.get(tileId)?.forEach((cb) => { try { cb(); } catch { /**/ } });
}

function subscribe(tileId: string, cb: () => void): () => void {
  if (!_SUBS.has(tileId)) _SUBS.set(tileId, new Set());
  _SUBS.get(tileId)!.add(cb);
  return () => _SUBS.get(tileId)?.delete(cb);
}

// Called by executeToolCall before spawnTileWithId. Idempotent — safe to call
// multiple times for the same tileId (duplicate calls are no-ops).
export function startSubAgentDispatch(tileId: string): void {
  if (_STARTED.has(tileId)) return;
  const job = _JOBS.get(tileId);
  if (!job) return;

  _STARTED.add(tileId);
  const state: RunState = { output: "", status: "running", errorMsg: null };
  _STATE.set(tileId, state);

  postDispatchSubagent(
    job.role, job.task, tileId,
    {
      onDelta: (chunk) => {
        const s = _STATE.get(tileId);
        if (s) { s.output += chunk; notify(tileId); }
      },
      onDone: () => {
        const s = _STATE.get(tileId);
        if (s) { s.status = "done"; notify(tileId); }
      },
      onError: (msg) => {
        const s = _STATE.get(tileId);
        if (s) { s.status = "error"; s.errorMsg = msg; notify(tileId); }
      },
    },
    job.modelTier,
  ).catch((e: unknown) => {
    const s = _STATE.get(tileId);
    if (s) {
      s.status = "error";
      s.errorMsg = e instanceof Error ? e.message : String(e);
      notify(tileId);
    }
  });
}

// ── Component ────────────────────────────────────────────────────────────────

export default function SubAgentTile() {
  const tileId = useTileId();
  const [tick, setTick] = useState(0);

  useEffect(() => {
    startSubAgentDispatch(tileId);
    return subscribe(tileId, () => setTick((n) => n + 1));
  }, [tileId]);

  const job   = _JOBS.get(tileId);
  const state = _STATE.get(tileId) ?? { output: "", status: "running" as const, errorMsg: null };
  void tick;

  const roleLabel = job?.role ?? "Sub-agent";
  const taskLabel = (job?.task ?? "").slice(0, 100) + ((job?.task ?? "").length > 100 ? "…" : "");

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title" style={{ textTransform: "capitalize" }}>{roleLabel}</span>
        <span className={`subagent-status-pill subagent-status-${state.status}`}>
          {state.status === "running" ? "running…" : state.status === "done" ? "done" : "error"}
        </span>
      </div>
      <div
        className="tile-body subagent-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {taskLabel && <div className="subagent-task">{taskLabel}</div>}
        <div className="subagent-output md-host">
          {state.output
            ? <MarkdownView text={state.output} />
            : (state.status === "running"
              ? <span className="subagent-thinking">thinking…</span>
              : null)}
          {state.status === "error" && state.errorMsg && (
            <div className="subagent-error">{state.errorMsg}</div>
          )}
        </div>
      </div>
    </div>
  );
}
