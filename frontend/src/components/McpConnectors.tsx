// MCP connectors — DEFAULT-DENY, explicit-consent. Browse a marketplace, connect
// any Streamable-HTTP MCP server, REVIEW the tools it exposes, then explicitly
// "Connect & trust" before any tool reaches Nova. Per-call approval is enforced
// elsewhere (the approval popup). Includes a global kill switch + an audit log.

import { useEffect, useState } from "react";
import {
  getMcpMarketplace, getMcpServers, getMcpTools, connectMcp, disconnectMcp,
  trustMcp, setMcpEnabled, disconnectAllMcp, getMcpAudit,
  type McpServer, type McpTool, type McpMarketItem, type McpAuditEvent,
} from "../api";

export default function McpConnectors() {
  const [market, setMarket] = useState<McpMarketItem[]>([]);
  const [servers, setServers] = useState<McpServer[]>([]);
  const [tools, setTools] = useState<McpTool[]>([]);             // EXPOSED (trusted) tools
  const [preview, setPreview] = useState<Record<string, McpTool[]>>({});  // tools previewed at connect (untrusted)
  const [enabled, setEnabled] = useState(true);
  const [audit, setAudit] = useState<McpAuditEvent[]>([]);
  const [showAudit, setShowAudit] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [auth, setAuth] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const refresh = async () => {
    try {
      const [s, t] = await Promise.all([getMcpServers(), getMcpTools()]);
      setServers(s.servers); setEnabled(s.enabled); setTools(t.tools);
    } catch { /* fail-closed */ }
  };
  useEffect(() => { getMcpMarketplace().then((m) => setMarket(m.items)).catch(() => {}); void refresh(); }, []);

  const connect = async () => {
    if (!url.trim()) return;
    setBusy(true); setErr("");
    try {
      const res = await connectMcp(name.trim(), url.trim(), auth.trim() || undefined);
      setPreview((p) => ({ ...p, [res.id]: res.tools }));   // stash for the untrusted review
      setName(""); setUrl(""); setAuth("");
      await refresh();
    } catch (e) { setErr(e instanceof Error ? e.message : "connect failed"); }
    finally { setBusy(false); }
  };
  const trust = async (id: string) => { try { await trustMcp(id, true); await refresh(); } catch { /* ignore */ } };
  const remove = async (id: string) => { try { await disconnectMcp(id); await refresh(); } catch { /* ignore */ } };
  const toggleEnabled = async () => { try { const r = await setMcpEnabled(!enabled); setEnabled(r.enabled); } catch { /* ignore */ } };
  // Destructive and previously unconfirmed. It also used to remove Nova's
  // built-in hands along with the user's servers; the backend keeps those now.
  const killAll = async () => {
    const mine = servers.filter((s) => !s.builtin).length;
    if (!mine) return;
    if (!window.confirm(`Remove ${mine} connected server${mine === 1 ? "" : "s"}? Nova's built-in tools stay.`)) return;
    try { await disconnectAllMcp(); setPreview({}); await refresh(); } catch { /* ignore */ }
  };
  const loadAudit = async () => { try { const a = await getMcpAudit(); setAudit(a.events); setShowAudit(true); } catch { setShowAudit(true); } };
  const pick = (m: McpMarketItem) => { setName(m.name === "Custom server" ? "" : m.name); setUrl(m.url_hint); setAuth(""); setErr(""); };

  const toolsFor = (s: McpServer): McpTool[] => s.trusted ? tools.filter((t) => t.server_id === s.id) : (preview[s.id] ?? []);

  return (
    <div className="mcp-wrap">
      {/* Kill switch header */}
      <div className="mcp-kill">
        <button type="button" className={`mcp-toggle${enabled ? " on" : ""}`} onClick={toggleEnabled} aria-pressed={enabled}>
          <span className="mcp-toggle-dot" /> MCP {enabled ? "on" : "OFF"}
        </button>
        <button type="button" className="mcp-link" onClick={loadAudit}>Activity log</button>
        {servers.some((s) => !s.builtin) && (
          <button type="button" className="mcp-link mcp-danger" onClick={killAll}>Disconnect all</button>
        )}
      </div>
      {!enabled && <div className="mcp-warn">MCP is off — no tools are exposed to Nova.</div>}

      {/* Marketplace chips */}
      <div className="mcp-market">
        {market.map((m) => (
          <button key={m.name} type="button" className="mcp-chip" onClick={() => pick(m)} title={m.desc}>
            <span className="mcp-chip-name">{m.name}</span>
            <span className="mcp-chip-cat">{m.category}</span>
          </button>
        ))}
      </div>

      {/* Add / connect form */}
      <div className="mcp-form">
        <input className="mcp-in" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} spellCheck={false} />
        <input className="mcp-in" placeholder="Server URL (https://host/mcp)" value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false} autoComplete="off" />
        <input className="mcp-in" placeholder="Bearer token (if required)" value={auth} onChange={(e) => setAuth(e.target.value)} type="password" autoComplete="off" />
        <button type="button" className="mcp-connect" onClick={connect} disabled={busy || !url.trim()}>
          {busy ? "Connecting…" : "Connect (review tools)"}
        </button>
      </div>
      {err && <div className="mcp-err">{err}</div>}

      {/* Connected servers */}
      {servers.length === 0 ? (
        <div className="mcp-empty">No MCP servers connected.</div>
      ) : (
        servers.map((s) => (
          <div key={s.id} className={`mcp-server${s.trusted ? "" : " untrusted"}`}>
            <div className="mcp-server-head">
              <span className="mcp-server-name">{s.name}</span>
              <span className={`mcp-badge ${s.trusted ? "ok" : "warn"}`}>{s.trusted ? "trusted" : "untrusted"}</span>
              <span className="mcp-server-meta">{s.trusted ? `${s.exposed} exposed` : `${(preview[s.id] ?? []).length} tools`}{s.has_auth ? " · auth" : ""}</span>
              {/* Nova's own hands are not a connection the user made, and
                  removing one only breaks the app until the next restart
                  re-seeds it. The backend refuses too — this just stops
                  offering a button whose only outcome is damage. */}
              {s.builtin
                ? <span className="mcp-server-meta">built in</span>
                : <button type="button" className="mcp-x" onClick={() => remove(s.id)}>Remove</button>}
            </div>
            <div className="mcp-server-url">{s.url}{s.insecure && <span className="mcp-insecure"> · ⚠ not https</span>}</div>
            <div className="mcp-tools">
              {toolsFor(s).map((t) => (
                <div key={t.name} className="mcp-tool">
                  <code className="mcp-tool-name">{t.name}</code>
                  <span className="mcp-tool-desc">{t.description}</span>
                </div>
              ))}
              {toolsFor(s).length === 0 && <div className="mcp-tool-desc">No tools.</div>}
            </div>
            {!s.trusted && (
              <div className="mcp-trust-row">
                <span className="mcp-trust-note">Review the tools above. Trusting exposes them to Nova (each call still needs your approval).</span>
                <button type="button" className="mcp-trust-btn" onClick={() => trust(s.id)}>Connect &amp; trust</button>
              </div>
            )}
          </div>
        ))
      )}

      {/* Audit log */}
      {showAudit && (
        <div className="mcp-audit">
          <div className="mcp-audit-h">Activity — MCP tool calls</div>
          {audit.length === 0 ? <div className="mcp-tool-desc">No activity yet.</div> : audit.map((e, i) => (
            <div key={i} className={`mcp-audit-row ${e.decision}`}>
              <span className="mcp-audit-dec">{e.decision}</span>
              <code>{e.server} · {e.tool}</code>
              <span className="mcp-audit-args" title={JSON.stringify(e.args)}>{JSON.stringify(e.args).slice(0, 60)}</span>
            </div>
          ))}
        </div>
      )}

      <div className="mcp-note">Untrusted servers expose ZERO tools. Every tool call is asked (no “always”). Tool descriptions + results are treated as untrusted data, never instructions.</div>
    </div>
  );
}
