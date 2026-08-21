// Mission Control tile — plan → approve → server-side DAG run → synthesize.
//
// v2: Approve POSTs the (edited) plan to /mission/run; the backend runtime
// executes it — generator→evaluator verification, bounded critique-retries,
// distilled cross-node context, one synthesis turn — and this tile becomes a
// live viewer over the NDJSON event stream. Stop is honored server-side.
//
// The Approve click IS the gate: nothing dispatches before /mission/run.
// Mirrors SubAgentTile's module-level record pattern so StrictMode remounts
// never abort an in-flight mission.

import { useEffect, useMemo, useState } from "react";
import { useTileId } from "../../state/TilesContext";
import TileGlyph from "../TileGlyph";
import {
  postMissionPlan, missionRun, missionEvents, missionStop,
  type MissionSnapshot, type MissionNodeBrief,
} from "../../api";
import MarkdownView from "../MarkdownView";

export type Subtask = {
  id: string;
  role: string;
  task: string;
  tier: "fast" | "smart";
  est_tokens: number;
  depends_on?: string[];
};

type Phase = "planning" | "review" | "running" | "synthesizing" | "done"
  | "stopped" | "error";

type Mission = {
  goal: string;
  model: string;              // chat model for planning + synthesis
  title: string;
  subtasks: Subtask[];        // editable plan (review phase)
  phase: Phase;
  serverId: string | null;    // backend mission id once running
  nodes: MissionNodeBrief[];  // live server state
  synthesis: string;
  error: string | null;
};

// Rough tier pricing for the live estimate (USD per 1M out-tokens dominate).
const TIER_EST_USD_PER_KTOK: Record<string, number> = { fast: 0.0006, smart: 0.015 };

const _M = new Map<string, Mission>();
const _STARTED = new Set<string>();
const _SUBS = new Map<string, Set<() => void>>();

function notify(id: string) { _SUBS.get(id)?.forEach((cb) => { try { cb(); } catch {} }); }
function subscribe(id: string, cb: () => void): () => void {
  if (!_SUBS.has(id)) _SUBS.set(id, new Set());
  _SUBS.get(id)!.add(cb);
  return () => _SUBS.get(id)?.delete(cb);
}

export function registerMission(tileId: string, goal: string, model: string): void {
  _M.set(tileId, {
    goal, model, title: "Mission", subtasks: [],
    phase: "planning", serverId: null, nodes: [], synthesis: "", error: null,
  });
}

export function startMissionPlan(tileId: string): void {
  if (_STARTED.has(tileId)) return;
  const m = _M.get(tileId);
  if (!m) return;
  _STARTED.add(tileId);

  postMissionPlan(m.goal, m.model)
    .then((plan) => {
      const cur = _M.get(tileId);
      if (!cur) return;
      cur.title = plan.title;
      cur.subtasks = plan.subtasks.map((s) => ({ ...s }));
      cur.phase = "review";
      notify(tileId);
    })
    .catch((e: unknown) => {
      const cur = _M.get(tileId);
      if (!cur) return;
      cur.phase = "error";
      cur.error = e instanceof Error ? e.message : String(e);
      notify(tileId);
    });
}

function applySnapshot(tileId: string, s: MissionSnapshot): void {
  const m = _M.get(tileId);
  if (!m) return;
  m.nodes = s.nodes;
  m.synthesis = s.synthesis;
  if (s.status === "running") m.phase = "running";
  else if (s.status === "synthesizing") m.phase = "synthesizing";
  else if (s.status === "done") m.phase = "done";
  else if (s.status === "stopped") m.phase = "stopped";
  else if (s.status === "failed") { m.phase = "error"; m.error = s.error || "mission failed"; }
  notify(tileId);
}

async function approveAndRun(tileId: string): Promise<void> {
  const m = _M.get(tileId);
  if (!m || m.phase !== "review" || !m.subtasks.length) return;
  m.phase = "running";
  m.error = null;
  notify(tileId);
  try {
    const snap = await missionRun({
      goal: m.goal,
      title: m.title,
      model: m.model,
      subtasks: m.subtasks.map((s) => ({
        id: s.id, role: s.role, task: s.task, tier: s.tier,
        est_tokens: s.est_tokens, depends_on: s.depends_on || [],
      })),
    });
    m.serverId = snap.id;
    applySnapshot(tileId, snap);
    // Live event stream until terminal; onError falls back to the last state.
    void missionEvents(snap.id,
      (s) => applySnapshot(tileId, s),
      (msg) => {
        const cur = _M.get(tileId);
        if (!cur || cur.phase === "done" || cur.phase === "stopped") return;
        cur.error = cur.error || msg;
        notify(tileId);
      });
  } catch (e) {
    m.phase = "error";
    m.error = e instanceof Error ? e.message : String(e);
    notify(tileId);
  }
}

// ── Component ────────────────────────────────────────────────────────
export default function MissionTile() {
  const tileId = useTileId();
  const [, force] = useState(0);

  useEffect(() => {
    startMissionPlan(tileId);
    return subscribe(tileId, () => force((n) => n + 1));
  }, [tileId]);

  const m = _M.get(tileId);

  const estUsd = useMemo(() => {
    if (!m) return 0;
    return m.subtasks.reduce((sum, s) =>
      sum + (s.est_tokens / 1000) * (TIER_EST_USD_PER_KTOK[s.tier] || 0.002), 0);
  }, [m, m?.subtasks.length, m?.phase]);

  if (!m) {
    return (
      <div className="tile"><div className="tile-head"><span className="tile-title"><TileGlyph />Mission</span></div>
        <div className="tile-body">No mission.</div></div>
    );
  }

  const editTask = (id: string, task: string) => {
    const st = m.subtasks.find((s) => s.id === id); if (st) { st.task = task; force((n) => n + 1); }
  };
  const toggleTier = (id: string) => {
    const st = m.subtasks.find((s) => s.id === id);
    if (st) { st.tier = st.tier === "fast" ? "smart" : "fast"; force((n) => n + 1); }
  };
  const removeTask = (id: string) => {
    m.subtasks = m.subtasks.filter((s) => s.id !== id);
    // Drop dangling deps so the server-side graph validation stays green.
    const ids = new Set(m.subtasks.map((s) => s.id));
    m.subtasks.forEach((s) => {
      s.depends_on = (s.depends_on || []).filter((d) => ids.has(d));
    });
    force((n) => n + 1);
  };

  const live = m.phase === "running" || m.phase === "synthesizing";
  const nodeById = new Map(m.nodes.map((n) => [n.id, n]));

  return (
    <div className="tile mission-tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />{m.title}</span>
        <span className="tile-title-meta">{phaseLabel(m.phase)}</span>
      </div>
      <div className="tile-body mission-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mission-goal">{m.goal}</div>

        {m.phase === "planning" && <div className="mission-hint">planning…</div>}
        {m.phase === "error" && <div className="subagent-error">{m.error}</div>}
        {m.phase === "stopped" && <div className="mission-hint">stopped.</div>}

        {m.subtasks.length > 0 && (
          <div className="mission-tasks">
            {m.subtasks.map((s, i) => {
              const live_n = nodeById.get(s.id);
              const status = live_n?.status ?? (m.phase === "review" ? undefined : "pending");
              return (
                <div key={s.id} className={`mission-task mission-task-${status || "pending"}`}>
                  <div className="mission-task-head">
                    <span className="mission-task-n">{i + 1}</span>
                    <span className="mission-task-role">{s.role}</span>
                    {(s.depends_on?.length ?? 0) > 0 && (
                      <span className="mission-dep" title="Runs after">↳ {s.depends_on!.join(", ")}</span>
                    )}
                    <button className="mission-tier-pill" onClick={() => toggleTier(s.id)}
                            disabled={m.phase !== "review"} title="Toggle tier">{s.tier}</button>
                    {m.phase === "review" && (
                      <button className="mission-task-x" onClick={() => removeTask(s.id)} title="Remove">×</button>
                    )}
                    {status && m.phase !== "review" && (
                      <span className={`subagent-status-pill subagent-status-${status}`}>
                        {status}{(live_n?.attempts ?? 0) > 1 ? ` ·${live_n!.attempts}` : ""}
                      </span>
                    )}
                  </div>
                  {m.phase === "review" ? (
                    <textarea className="mission-task-edit" value={s.task}
                              onChange={(e) => editTask(s.id, e.target.value)} rows={2} />
                  ) : (
                    <>
                      <div className="mission-task-text">{s.task}</div>
                      {live_n && live_n.status === "running" && live_n.output_chars > 0 && (
                        <div className="mission-hint">…{live_n.output_chars} chars</div>
                      )}
                      {live_n?.status === "verifying" && (
                        <div className="mission-hint">verifying against contract…</div>
                      )}
                      {live_n?.distilled && (
                        <div className="mission-task-out md-host"><MarkdownView text={live_n.distilled} /></div>
                      )}
                      {live_n?.error && <div className="subagent-error">{live_n.error}</div>}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {m.phase === "review" && (
          <div className="mission-actions">
            <span className="mission-est">~${estUsd.toFixed(3)} est.</span>
            <button className="mission-approve" onClick={() => void approveAndRun(tileId)}>Approve · run</button>
          </div>
        )}
        {live && m.serverId && (
          <div className="mission-actions">
            <button className="mission-stop"
                    onClick={() => { void missionStop(m.serverId!); }}>Stop</button>
          </div>
        )}

        {(m.phase === "synthesizing" || m.phase === "done") && (
          <div className="mission-synthesis">
            <div className="mission-synthesis-label">{m.phase === "synthesizing" ? "synthesizing…" : "Result"}</div>
            <div className="md-host"><MarkdownView text={m.synthesis} /></div>
          </div>
        )}
      </div>
    </div>
  );
}

function phaseLabel(p: Phase): string {
  return p === "planning" ? "planning"
    : p === "review" ? "review plan"
    : p === "running" ? "running (verified)"
    : p === "synthesizing" ? "synthesizing"
    : p === "done" ? "done"
    : p === "stopped" ? "stopped" : "error";
}
