// Usage tile — per-provider API usage summary.
// Chat providers: connection state + model count.
// Dispatch (OpenRouter): real usage/limit from /api/v1/auth/key.

import { useCallback, useEffect, useRef, useState } from "react";
import { getUsageAll, getSpendSummary, postSpendCap, getVaultStats, getMissionRecent, type UsageAll, type SpendSummary } from "../../api";
import TileGlyph from "../TileGlyph";

function usd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `$${n.toFixed(4)}`;
}

// One terse system line — vault size + live missions. Same data that briefly
// lived in a top-right HUD strip; one datum, one home (this tile).
function SystemLine() {
  const [txt, setTxt] = useState<string>("");
  useEffect(() => {
    let alive = true;
    const load = async () => {
      const parts: string[] = [];
      try {
        const v = await getVaultStats();
        parts.push(`vault ${v.nodes} nd · ${v.edges} ln`);
      } catch { /* vault quiet */ }
      try {
        const { items } = await getMissionRecent(20);
        const live = items.filter((m) => m.status === "running" || m.status === "synthesizing").length;
        if (live > 0) parts.push(`${live} mission${live > 1 ? "s" : ""} live`);
      } catch { /* missions quiet */ }
      if (alive) setTxt(parts.join("   ·   "));
    };
    void load();
    const t = window.setInterval(load, 30_000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  if (!txt) return null;
  return <div className="usage-system-line">{txt}</div>;
}
function usd2(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `$${n.toFixed(2)}`;
}

// Live month-to-date spend + budget cap. Polls while the tile is open so the
// meter visibly ticks during a mission.
function SpendHud() {
  const [s, setS] = useState<SpendSummary | null>(null);
  const [capDraft, setCapDraft] = useState<string>("");
  const [editingCap, setEditingCap] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    try { setS(await getSpendSummary()); } catch { /* leave prior */ }
  }, []);

  useEffect(() => {
    void load();
    timer.current = window.setInterval(load, 4000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [load]);

  if (!s) return null;

  const cap = s.cap_usd;
  const pct = cap && cap > 0 ? Math.min(100, (s.total_usd / cap) * 100) : 0;
  const missions = Object.entries(s.by_mission).sort(([, a], [, b]) => b - a).slice(0, 4);

  const saveCap = async () => {
    const v = parseFloat(capDraft);
    try { setS(await postSpendCap(isNaN(v) || v <= 0 ? null : v)); } catch {}
    setEditingCap(false);
  };

  return (
    <div className="spend-hud">
      <div className="usage-section-label">Spend · {s.month}</div>
      <div className="spend-total-row">
        <span className="spend-total">{usd2(s.total_usd)}</span>
        <span className="spend-total-sub">this month</span>
        {editingCap ? (
          <span className="spend-cap-edit">
            <input className="spend-cap-input" type="number" min="0" step="1"
                   value={capDraft} placeholder="cap $"
                   onChange={(e) => setCapDraft(e.target.value)}
                   onKeyDown={(e) => { if (e.key === "Enter") void saveCap(); }} autoFocus />
            <button className="spend-cap-save" onClick={() => void saveCap()}>set</button>
          </span>
        ) : (
          <button className="spend-cap-btn"
                  onClick={() => { setCapDraft(cap ? String(cap) : ""); setEditingCap(true); }}
                  title="Set monthly cap">
            {cap ? `cap ${usd2(cap)}` : "set cap"}
          </button>
        )}
      </div>
      {cap && cap > 0 && (
        <div className="usage-bar-wrap">
          <div className={`usage-bar-fill ${s.over_cap ? "usage-bar-danger" : s.warn ? "usage-bar-warn" : ""}`}
               style={{ width: `${pct}%` }} />
        </div>
      )}
      {s.over_cap && <div className="spend-capped-note">Cap reached — frontier calls fall back to the local model.</div>}
      {!s.over_cap && s.warn && <div className="spend-warn-note">Approaching cap (80%+).</div>}
      {missions.length > 0 && (
        <div className="spend-missions">
          {missions.map(([id, v]) => (
            <div key={id} className="spend-mission-row">
              <span className="spend-mission-id">mission {id.slice(-4)}</span>
              <span className="spend-mission-val">{usd(v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UsageBar({ used, limit }: { used: number; limit: number | null | undefined }) {
  if (!limit || limit <= 0) return null;
  const pct = Math.min(100, (used / limit) * 100);
  return (
    <div className="usage-bar-wrap">
      <div
        className={`usage-bar-fill ${pct > 85 ? "usage-bar-danger" : ""}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export default function UsageTile() {
  const [data, setData]       = useState<UsageAll | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getUsageAll();
      setData(d);
      setLastRefresh(new Date());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load usage");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const chatEntries = data ? Object.entries(data.chat).sort(([a], [b]) => a.localeCompare(b)) : [];
  const or = data?.dispatch.openrouter;

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />API Usage</span>
        <button
          type="button"
          className="usage-refresh-btn"
          onClick={() => void load()}
          disabled={loading}
          title="Refresh"
          aria-label="Refresh"
        >
          <svg viewBox="0 0 14 14" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
            <path d="M12 7 A5 5 0 1 1 7 2" strokeLinecap="round" />
            <path d="M7 2 L9.5 2 L9.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <div
        className="tile-body usage-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {error && <div className="usage-error">{error}</div>}

        <SpendHud />
        <SystemLine />
        <div className="usage-divider" />

        {data && (
          <>
            {/* "BYOK" is our word, not the reader's. Matches the wording in
                Settings → Connections so the two screens name one thing once. */}
            <div className="usage-section-label">Your own provider keys</div>
            {chatEntries.map(([key, info]) => (
              <div key={key} className="usage-row">
                <span className={`usage-dot ${info.connected ? "usage-dot-on" : "usage-dot-off"}`} />
                <span className="usage-prov-name">{info.label}</span>
                <span className="usage-prov-detail">
                  {info.connected
                    ? `${info.model_count} model${info.model_count !== 1 ? "s" : ""}`
                    : "not connected"}
                </span>
              </div>
            ))}

            <div className="usage-divider" />

            {/* This row named the upstream provider twice. The spend it
                reports is real and worth showing; which wholesaler we buy it
                through is not the user's concern, and they cannot connect or
                disconnect it — the product owns that key. */}
            <div className="usage-section-label">Background tasks</div>
            <div className="usage-row">
              <span className={`usage-dot ${or?.connected ? "usage-dot-on" : "usage-dot-off"}`} />
              <span className="usage-prov-name">Sub-agents</span>
              <span className="usage-prov-detail">
                {or?.connected ? "available" : "unavailable"}
              </span>
            </div>

            {or?.connected && or.usage_usd !== null && or.usage_usd !== undefined && (
              <div className="usage-or-detail">
                <div className="usage-or-row">
                  <span className="usage-or-label">Used</span>
                  <span className="usage-or-val">{usd(or.usage_usd)}</span>
                  {or.limit_usd && (
                    <>
                      <span className="usage-or-label" style={{ marginLeft: 10 }}>Limit</span>
                      <span className="usage-or-val">{usd(or.limit_usd)}</span>
                    </>
                  )}
                </div>
                <UsageBar used={or.usage_usd} limit={or.limit_usd} />
              </div>
            )}

            {or?.connected && !or.limit_usd && (
              <div className="usage-or-note">No credit limit — usage tracked but unlimited.</div>
            )}
          </>
        )}

        {lastRefresh && (
          <div className="usage-footer">Updated {lastRefresh.toLocaleTimeString()}</div>
        )}
      </div>
    </div>
  );
}
