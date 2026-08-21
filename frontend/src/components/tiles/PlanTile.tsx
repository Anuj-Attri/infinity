// Plan tile — a thorough, milestone-structured plan from /plan. Multi-instance
// (each plan kept, slugged /plan-<name>, referenceable downstream). Shows a
// progress bar across the recipe (research → scope → diagram → synthesize), then
// the streamed plan markdown. Read-only; populated by App.runPlan via planStore.
// It PLANS, it does not execute — the user references it to Nemo downstream.

import { useEffect, useState } from "react";
import MarkdownView from "../MarkdownView";
import TileGlyph from "../TileGlyph";
import { useTileId } from "../../state/TilesContext";
import { getPlan, subscribePlan, type PlanStage } from "../../state/planStore";

const STAGE_META: Record<PlanStage, { pct: number; label: string }> = {
  researching:  { pct: 22, label: "Researching the goal…" },
  scoping:      { pct: 45, label: "Scoping milestones + BOM…" },
  diagramming:  { pct: 62, label: "Drawing the diagram…" },
  synthesizing: { pct: 85, label: "Writing the plan…" },
  done:         { pct: 100, label: "Done" },
  error:        { pct: 100, label: "Stopped" },
};

export default function PlanTile() {
  const tileId = useTileId();
  const [, setTick] = useState(0);
  useEffect(() => subscribePlan(tileId, () => setTick((n) => n + 1)), [tileId]);

  const p = getPlan(tileId);
  const meta = p ? STAGE_META[p.stage] : null;
  const inProgress = p && p.stage !== "done" && p.stage !== "error";

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Plan</span>
        {p?.slug && <span className="tile-title-meta">/{p.slug}</span>}
      </div>
      <div
        className="tile-body tplan-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {!p ? (
          <div className="tplan-empty">/plan a goal</div>
        ) : (
          <>
            {(inProgress || p.stage === "error") && meta && (
              <div className="tresearch-progress">
                <div className="tresearch-bar">
                  <div className={`tresearch-bar-fill ${p.stage === "error" ? "err" : ""}`} style={{ width: `${meta.pct}%` }} />
                </div>
                <div className="tresearch-stage">{meta.label}{p.note ? ` · ${p.note}` : ""}</div>
              </div>
            )}
            {p.stage === "error" ? (
              <div className="tplan-err">Plan unavailable.</div>
            ) : p.text ? (
              <div className="tplan-text md-host"><MarkdownView text={p.text} /></div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
