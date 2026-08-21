// Research tile — balanced, trust-calibrated source-check from /research.
// Ephemeral last-only (mirrors SummaryTile). Shows a PROGRESS BAR across the
// multi-step flow (searching the web → assessing → streaming the brief), then
// the streamed markdown assessment. Read-only; populated by App.runResearch.

import { useSyncExternalStore } from "react";
import MarkdownView from "../MarkdownView";
import TileGlyph from "../TileGlyph";
import { getResearch, subscribeResearch, type ResearchStage } from "../../state/researchStore";

// Stage → progress fraction + label. "streaming" rides up as text arrives.
const STAGE_META: Record<ResearchStage, { pct: number; label: string }> = {
  searching: { pct: 30, label: "Searching the web…" },
  assessing: { pct: 60, label: "Assessing sources…" },
  streaming: { pct: 90, label: "Writing the brief…" },
  done:      { pct: 100, label: "Done" },
  error:     { pct: 100, label: "Failed" },
};

export default function ResearchTile() {
  const r = useSyncExternalStore(subscribeResearch, getResearch);
  const meta = r ? STAGE_META[r.stage] : null;
  const inProgress = r && r.stage !== "done" && r.stage !== "error";

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Research</span>
        <span className="tile-title-meta">{r ? r.query : ""}</span>
      </div>
      <div
        className="tile-body tresearch-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {!r ? (
          <div className="tresearch-empty">/research a topic or a source</div>
        ) : (
          <>
            {(inProgress || r.stage === "error") && meta && (
              <div className="tresearch-progress">
                <div className="tresearch-bar">
                  <div
                    className={`tresearch-bar-fill ${r.stage === "error" ? "err" : ""}`}
                    style={{ width: `${meta.pct}%` }}
                  />
                </div>
                <div className="tresearch-stage">{meta.label}</div>
              </div>
            )}
            {r.stage === "error" ? (
              <div className="tresearch-err">Research unavailable.</div>
            ) : r.text ? (
              <div className="tresearch-text md-host"><MarkdownView text={r.text} /></div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
