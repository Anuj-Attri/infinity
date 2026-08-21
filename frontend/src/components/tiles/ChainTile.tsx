// Chain tile — live progress of a multi-step /-command pipeline, plus the
// gate-prompt when a CRITICAL step fails. Ephemeral last-only (mirrors Summary/
// Research). The gate is an AskUserQuestion-style choice rendered in-tile (the
// UI is tile-based): Nova explains what broke and the user picks Retry / Skip /
// Abort, or types guidance and retries. The pick resolves the awaiting chain.

import { useState } from "react";
import { useSyncExternalStore } from "react";
import TileGlyph from "../TileGlyph";
import { getChain, subscribeChain, resolveGate, type ChainStepStatus } from "../../state/chainStore";

const STATUS_GLYPH: Record<ChainStepStatus, string> = {
  pending: "○", running: "◐", done: "●", skipped: "◌", failed: "✕",
};

export default function ChainTile() {
  const c = useSyncExternalStore(subscribeChain, getChain);
  const [guidance, setGuidance] = useState("");

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Chain</span>
        <span className="tile-title-meta">
          {c ? (c.status === "running" ? "running…" : c.status) : ""}
        </span>
      </div>
      <div
        className="tile-body tchain-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {!c ? (
          <div className="tchain-empty">Run a chain of /-commands</div>
        ) : (
          <>
            <ol className="tchain-steps">
              {c.steps.map((s, i) => (
                <li key={i} className={`tchain-step tchain-${s.status} ${i === c.active ? "active" : ""}`}>
                  <span className={`tchain-glyph tchain-glyph-${s.status}`}>{STATUS_GLYPH[s.status]}</span>
                  <span className="tchain-label">/{s.cmd}</span>
                  <span className="tchain-meta">
                    {s.status === "running" && (s.stage || "running…")}
                    {s.status === "skipped" && `skipped — ${s.note || ""}`}
                    {s.status === "failed" && (s.note || "failed")}
                  </span>
                </li>
              ))}
            </ol>

            {/* Gate prompt — only while a critical step awaits the user. */}
            {c.gate && (
              <div className="tchain-gate">
                <div className="tchain-gate-h">/{c.gate.label} couldn't continue</div>
                <div className="tchain-gate-reason">{c.gate.reason}</div>
                <div className="tchain-gate-actions">
                  <button type="button" className="tchain-btn primary"
                    onClick={() => resolveGate({ action: "retry", guidance: guidance.trim() || undefined })}>
                    Retry
                  </button>
                  <button type="button" className="tchain-btn"
                    onClick={() => resolveGate({ action: "skip" })}>Skip</button>
                  <button type="button" className="tchain-btn"
                    onClick={() => resolveGate({ action: "abort" })}>Abort</button>
                </div>
                <input
                  className="tchain-gate-input"
                  placeholder="…or tell Nova how to proceed, then Retry"
                  value={guidance}
                  onChange={(e) => setGuidance(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && guidance.trim()) {
                      resolveGate({ action: "retry", guidance: guidance.trim() });
                    }
                  }}
                />
              </div>
            )}

            {c.status !== "running" && c.summary && (
              <div className="tchain-summary">{c.summary}</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
