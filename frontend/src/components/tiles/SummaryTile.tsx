// Summary tile — shows the output of the LAST /summarize only. Ephemeral:
// stores nothing, no history, a new summarize replaces it. Standard tile.

import { useSyncExternalStore } from "react";
import MarkdownView from "../MarkdownView";
import TileGlyph from "../TileGlyph";
import { getSummary, subscribeSummary } from "../../state/summaryStore";

export default function SummaryTile() {
  const r = useSyncExternalStore(subscribeSummary, getSummary);

  const srcLabel = r && r.sources.length ? r.sources.map((s) => `/${s}`).join(" ") : "";

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Summary</span>
        <span className="tile-title-meta">{srcLabel}</span>
      </div>
      <div
        className="tile-body tsummary-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {!r ? (
          <div className="tsummary-empty">/summarize a tile to see it here</div>
        ) : r.loading ? (
          <div className="tsummary-empty">summarizing…</div>
        ) : r.error ? (
          <div className="tsummary-error">{r.error}</div>
        ) : (
          <>
            {r.focus && <div className="tsummary-focus">Focus: {r.focus}</div>}
            <div className="tsummary-text md-host"><MarkdownView text={r.summary} /></div>
          </>
        )}
      </div>
    </div>
  );
}
