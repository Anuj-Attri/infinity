// Console — the operator's one screen for "what is actually working?".
//
// Written for the moment something is quietly wrong. A hosted service degrades
// without announcing it: an unset key, an embedder that cannot reach anything, a
// keystore that stopped encrypting. Each looks like "the app is a bit broken"
// from outside, and the alternative to this tile is reading container logs.
//
// Every row states a status AND the single thing that would clear it. A status
// with no route out is just a complaint.
//
// It never renders a secret's value — the backend only reports whether each key
// is set. A diagnostics screen that prints keys leaks them the first time
// someone screenshots it.

import { useCallback, useEffect, useState } from "react";
import TileGlyph from "../TileGlyph";
import { getDiagnostics, type Diagnostics, type SubsystemStatus } from "../../api";

const DOT: Record<SubsystemStatus, string> = {
  ok: "var(--color-success, #3ba55d)",
  degraded: "var(--color-info, #d8a53a)",
  blocked: "var(--color-error, #d05353)",
  off: "var(--ink-4, #888)",
};

function Dot({ status }: { status: SubsystemStatus }) {
  return (
    <span
      aria-label={status}
      title={status}
      style={{
        display: "inline-block", width: 7, height: 7, borderRadius: "50%",
        background: DOT[status], flex: "0 0 auto", marginTop: 6,
      }}
    />
  );
}

export default function ConsoleTile() {
  const [d, setD] = useState<Diagnostics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      setD(await getDiagnostics());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const notOk = d ? d.subsystems.filter((s) => s.status !== "ok").length : 0;

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Console</span>
        <span className="tile-title-meta">
          {d ? (notOk ? `${notOk} need attention` : "all clear") : busy ? "checking…" : ""}
        </span>
      </div>

      <div
        className="tile-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        style={{ overflowY: "auto", padding: "4px 2px", fontSize: "0.86em", lineHeight: 1.45 }}
      >
        {err && <div className="authgate-error" style={{ marginBottom: 10 }}>{err}</div>}
        {!d && !err && <div style={{ color: "var(--ink-3)" }}>checking…</div>}

        {d && (
          <>
            <Section title="Subsystems">
              {d.subsystems.map((s) => (
                <div key={s.id} style={{ display: "flex", gap: 9, marginBottom: 9 }}>
                  <Dot status={s.status} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ color: "var(--ink-0)" }}>{s.name}</div>
                    <div style={{ color: "var(--ink-3)" }}>{s.detail}</div>
                    {s.unblock && (
                      <div style={{ color: "var(--accent)", marginTop: 2 }}>→ {s.unblock}</div>
                    )}
                  </div>
                </div>
              ))}
            </Section>

            <Section title="Configuration">
              <div style={{ color: "var(--ink-3)", marginBottom: 6 }}>
                Values are never sent to this screen — only whether each is set.
              </div>
              {d.config.map((c) => (
                <div key={c.name} style={{ display: "flex", gap: 9, marginBottom: 6 }}>
                  <Dot status={c.set ? "ok" : c.required ? "blocked" : "off"} />
                  <div style={{ minWidth: 0 }}>
                    <code style={{ color: "var(--ink-0)" }}>{c.name}</code>
                    {c.required && !c.set && (
                      <span style={{ color: "var(--color-error)" }}> · required</span>
                    )}
                    <div style={{ color: "var(--ink-3)" }}>{c.enables}</div>
                  </div>
                </div>
              ))}
            </Section>

            <Section title="Connectors">
              <Row k="Chat providers" v={
                Object.entries(d.connectors.chat_providers || {})
                  .map(([k, v]) => `${k}${(v as { connected?: boolean })?.connected ? "" : " (not connected)"}`)
                  .join(", ") || "none"
              } />
              <Row k="MCP" v={
                d.connectors.mcp?.error
                  ? `error: ${d.connectors.mcp.error}`
                  : `${d.connectors.mcp?.enabled ? "enabled" : "disabled"} · ${
                      (d.connectors.mcp?.servers || [])
                        .map((s) => `${s.name}${s.trusted ? "" : " (untrusted)"}`)
                        .join(", ") || "no servers"
                    }`
              } />
            </Section>

            <Section title="Service">
              <Row k="Auth" v={`${d.auth.password_set ? "configured" : "unclaimed"} · ${d.auth.devices} device(s) · keystore ${d.auth.store_backend}`} />
              <Row k="Runtime" v={`Python ${d.service.python} · ${d.service.containerised ? "container" : "local"}`} />
              <Row k="Platform" v={String(d.service.platform)} />
            </Section>

            <button
              className="authgate-btn"
              onClick={() => void refresh()}
              disabled={busy}
              style={{ marginTop: 4 }}
            >
              {busy ? "Checking…" : "Re-check"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{
        color: "var(--ink-2)", textTransform: "uppercase",
        letterSpacing: "0.08em", fontSize: "0.78em", marginBottom: 7,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: 5 }}>
      <span style={{ color: "var(--ink-2)", flex: "0 0 108px" }}>{k}</span>
      <span style={{ color: "var(--ink-1)", minWidth: 0, wordBreak: "break-word" }}>{v}</span>
    </div>
  );
}
