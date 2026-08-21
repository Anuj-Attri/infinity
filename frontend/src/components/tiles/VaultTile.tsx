// The Vault tile — window into the second brain.
//
// Tree: icon-led domain folders → notes (white symbols, not bare text).
// Graph: a living constellation — small white dots with bloom, clustered by
//   domain, faint orbital rings + a central glow, entrance animation, hover
//   (bloom + label + neighbor highlight, dim the rest), click → open the note.
// Search: hybrid retrieval. Ingest: drop / paste / URL / file → auto-filed.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import TileGlyph from "../TileGlyph";
import MarkdownView from "../MarkdownView";
import {
  getVaultStats, getVaultTree, getVaultGraph, vaultSearch, vaultIngest,
  vaultIngestFile, getVaultNode, vaultReindex,
  type VaultStats, type VaultTreeNode, type VaultGraph, type VaultHit,
} from "../../api";
import { DomainIcon, SourceIcon, TabIcon } from "./vaultIcons";

type Tab = "graph" | "tree" | "search";

export default function VaultTile() {
  const [tab, setTab] = useState<Tab>("graph");
  const [stats, setStats] = useState<VaultStats | null>(null);
  const [tree, setTree] = useState<Record<string, VaultTreeNode>>({});
  const [graph, setGraph] = useState<VaultGraph | null>(null);
  const [hits, setHits] = useState<VaultHit[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<{ title: string; body: string; domain: string[]; source: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([getVaultStats(), getVaultTree()]);
      setStats(s); setTree(t.tree); setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (tab === "graph" && !graph) {
      getVaultGraph().then(setGraph).catch(() => setGraph({ nodes: [], edges: [] }));
    }
  }, [tab, graph]);

  const doSearch = useCallback(async () => {
    const query = q.trim(); if (!query) return;
    setBusy("searching");
    try { const { items } = await vaultSearch(query, 12); setHits(items); setTab("search"); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }, [q]);

  const openNote = useCallback(async (idOrSlug: string) => {
    try {
      const n = await getVaultNode(idOrSlug);
      setOpen({ title: n.title, body: n.body, domain: n.domain, source: n.source?.kind || "note" });
    } catch { setErr("note unavailable"); }
  }, []);

  const ingestFiles = useCallback(async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      setBusy(`filing ${f.name}`);
      try { await vaultIngestFile(f, f.name); }
      catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    }
    setBusy(null); setGraph(null); void refresh();
  }, [refresh]);

  const ingestText = useCallback(async (text: string) => {
    const t = text.trim(); if (!t) return;
    setBusy("filing");
    try {
      if (/^https?:\/\/\S+$/i.test(t)) await vaultIngest({ url: t });
      else await vaultIngest({ text: t });
      setErr(null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); setGraph(null); void refresh(); }
  }, [refresh]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files?.length) void ingestFiles(e.dataTransfer.files);
    else { const t = e.dataTransfer.getData("text/plain"); if (t) void ingestText(t); }
  }, [ingestFiles, ingestText]);

  const TABS: Array<[Tab, "graph" | "tree" | "search"]> = [
    ["graph", "graph"], ["tree", "tree"], ["search", "search"],
  ];

  return (
    <div className="tile vault-tile" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Vault</span>
        {stats && (
          <span className="vault-metrics" title={stats.dir}>
            {stats.nodes} · {stats.domains}◆ · {stats.edges}⋯
          </span>
        )}
      </div>
      <div className="tile-body vault-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
        <div className="vault-bar">
          <input className="vault-search" placeholder="Search the vault" value={q}
                 onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") void doSearch(); }} />
          <div className="vault-tabs">
            {TABS.map(([t, icon]) => (
              <button key={t} className={`vault-tab ${tab === t ? "on" : ""}`}
                      title={t} aria-label={t} onClick={() => setTab(t)}>
                <TabIcon name={icon} />
              </button>
            ))}
          </div>
          <button className="vault-act" title="Add file" aria-label="Add file"
                  onClick={() => fileRef.current?.click()}><TabIcon name="plus" /></button>
          <button className="vault-act" title="Reindex" aria-label="Reindex"
                  onClick={() => { setBusy("reindexing"); vaultReindex().finally(() => { setBusy(null); setGraph(null); void refresh(); }); }}>
            <TabIcon name="refresh" /></button>
          <input ref={fileRef} type="file" multiple hidden
                 accept=".md,.txt,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.webp,.wav,.mp3,.m4a,.ogg,.webm,.flac"
                 onChange={(e) => { if (e.target.files?.length) void ingestFiles(e.target.files); e.target.value = ""; }} />
        </div>

        {busy && <div className="vault-hint">{busy}…</div>}
        {err && <div className="vault-err">{err}</div>}

        {open ? (
          <div className="vault-note">
            <div className="vault-note-head">
              <button className="vault-back" onClick={() => setOpen(null)}>←</button>
              <span className="vault-note-ic"><SourceIcon kind={open.source} size={15} /></span>
              <span className="vault-note-title">{open.title}</span>
              <span className="vault-note-domain">{open.domain.join(" / ")}</span>
            </div>
            <div className="md-host vault-note-body"><MarkdownView text={open.body} /></div>
          </div>
        ) : tab === "graph" ? (
          <GraphView graph={graph} onOpen={openNote} />
        ) : tab === "tree" ? (
          <TreeView tree={tree} onOpen={openNote} onIngestText={ingestText} />
        ) : (
          <div className="vault-hits">
            {hits.length === 0 && <div className="vault-hint">No results.</div>}
            {hits.map((h) => (
              <button key={h.node_id + h.snippet.slice(0, 12)} className="vault-hit" onClick={() => void openNote(h.node_id)}>
                <span className="vault-hit-ic"><DomainIcon name={h.domain[h.domain.length - 1] || h.domain[0] || ""} size={16} /></span>
                <span className="vault-hit-main">
                  <span className="vault-hit-title">{h.title}
                    <span className="vault-hit-domain">{h.domain.join(" / ")}</span></span>
                  <span className="vault-hit-snippet">{h.snippet}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Tree (icon-led, monochrome) ─────────────────────────────────────────────

function TreeView({ tree, onOpen, onIngestText }: {
  tree: Record<string, VaultTreeNode>; onOpen: (id: string) => void; onIngestText: (t: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const empty = Object.keys(tree).length === 0;
  return (
    <div className="vault-tree">
      {empty && <div className="vault-hint">Empty vault. Drop a file, paste text, or a URL — it files itself.</div>}
      {Object.entries(tree).map(([name, node]) => (
        <Branch key={name} name={name} node={node} depth={0} onOpen={onOpen} />
      ))}
      <div className="vault-quickadd">
        <textarea className="vault-quickadd-text" placeholder="Paste text or a URL — Enter files it"
                  value={draft} rows={2} onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onIngestText(draft); setDraft(""); } }} />
      </div>
    </div>
  );
}

function Branch({ name, node, depth, onOpen }: {
  name: string; node: VaultTreeNode; depth: number; onOpen: (id: string) => void;
}) {
  const [openB, setOpenB] = useState(depth < 1);
  const kids = Object.entries(node.children || {});
  const notes = node.notes || [];
  return (
    <div className="vault-branch" style={{ marginLeft: depth ? 14 : 0 }}>
      <button className="vault-branch-h" onClick={() => setOpenB((o) => !o)}>
        <span className={`vault-caret ${openB ? "open" : ""}`}>›</span>
        <span className="vault-branch-ic"><DomainIcon name={name} /></span>
        <span className="vault-branch-name">{name}</span>
        <span className="vault-branch-n">{notes.length + kids.length}</span>
      </button>
      {openB && (
        <div className="vault-branch-kids">
          {kids.map(([k, n]) => <Branch key={k} name={k} node={n} depth={depth + 1} onOpen={onOpen} />)}
          {notes.map((n) => (
            <button key={n.id} className="vault-leaf" onClick={() => onOpen(n.id)} style={{ marginLeft: 14 }}>
              <span className="vault-leaf-ic"><SourceIcon kind="note" /></span>
              <span className="vault-leaf-title">{n.title}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Graph — living constellation (white dots + bloom) ───────────────────────

type GNode = { id: string; domain: string[]; degree: number; x: number; y: number; vx: number; vy: number };

function GraphView({ graph, onOpen }: { graph: VaultGraph | null; onOpen: (id: string) => void }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef<Map<string, SVGGElement>>(new Map());
  const edgeRefs = useRef<(SVGLineElement | null)[]>([]);
  const posRef = useRef<Map<string, GNode>>(new Map());
  const [size, setSize] = useState({ w: 600, h: 460 });
  const [hovered, setHovered] = useState<string | null>(null);

  const model = useMemo(() => {
    const pos = posRef.current; pos.clear();
    if (!graph) return null;
    const nodes = graph.nodes;
    const edges = graph.edges.filter((e) => e.src !== e.dst);
    const domains = Array.from(new Set(nodes.map((n) => n.domain[0] || "Inbox")));
    const domIndex = new Map(domains.map((d, i) => [d, i]));
    nodes.forEach((n, i) => {
      const d0 = n.domain[0] || "Inbox";
      const sector = (domIndex.get(d0)! / Math.max(1, domains.length)) * Math.PI * 2;
      const jitter = (i % 5) * 0.18;
      const rad = 90 + (i % 6) * 26;
      pos.set(n.id, { id: n.id, domain: n.domain, degree: n.degree,
        x: Math.cos(sector + jitter) * rad, y: Math.sin(sector + jitter) * rad, vx: 0, vy: 0 });
    });
    const adj = new Map<string, Set<string>>();
    for (const e of edges) {
      if (!adj.has(e.src)) adj.set(e.src, new Set());
      if (!adj.has(e.dst)) adj.set(e.dst, new Set());
      adj.get(e.src)!.add(e.dst); adj.get(e.dst)!.add(e.src);
    }
    const centroids = new Map<string, { x: number; y: number }>();
    domains.forEach((d, i) => {
      const a = (i / Math.max(1, domains.length)) * Math.PI * 2;
      centroids.set(d, { x: Math.cos(a) * 150, y: Math.sin(a) * 150 });
    });
    return { nodes, edges, adj, centroids };
  }, [graph]);

  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) setSize({ w: r.width, h: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!model || !model.nodes.length) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    let raf = 0, alive = true, ticks = 0;
    const pos = posRef.current;
    const step = () => {
      if (!alive) return;
      ticks++;
      const settle = ticks < 600 && !reduced;
      if (settle) {
        for (const a of model.nodes) {
          const pa = pos.get(a.id)!;
          for (const b of model.nodes) {
            if (a.id >= b.id) continue;
            const pb = pos.get(b.id)!;
            let dx = pa.x - pb.x, dy = pa.y - pb.y;
            const d2 = Math.max(50, dx * dx + dy * dy);
            const f = 1400 / d2, d = Math.sqrt(d2);
            dx /= d; dy /= d;
            pa.vx += dx * f; pa.vy += dy * f; pb.vx -= dx * f; pb.vy -= dy * f;
          }
          const c = model.centroids.get(a.domain[0] || "Inbox");
          if (c) { pa.vx += (c.x - pa.x) * 0.004; pa.vy += (c.y - pa.y) * 0.004; }
          pa.vx -= pa.x * 0.0016; pa.vy -= pa.y * 0.0016;
        }
        for (const e of model.edges) {
          const ps = pos.get(e.src), pd = pos.get(e.dst);
          if (!ps || !pd) continue;
          const dx = pd.x - ps.x, dy = pd.y - ps.y;
          ps.vx += dx * 0.0025; ps.vy += dy * 0.0025;
          pd.vx -= dx * 0.0025; pd.vy -= dy * 0.0025;
        }
        for (const n of model.nodes) {
          const p = pos.get(n.id)!;
          p.vx *= 0.85; p.vy *= 0.85; p.x += p.vx; p.y += p.vy;
        }
      }
      const ox = size.w / 2, oy = size.h / 2;
      for (const n of model.nodes) {
        const p = pos.get(n.id)!; const g = nodeRefs.current.get(n.id);
        if (g) g.setAttribute("transform", `translate(${ox + p.x},${oy + p.y})`);
      }
      model.edges.forEach((e, i) => {
        const ln = edgeRefs.current[i]; const ps = pos.get(e.src), pd = pos.get(e.dst);
        if (ln && ps && pd) {
          ln.setAttribute("x1", `${ox + ps.x}`); ln.setAttribute("y1", `${oy + ps.y}`);
          ln.setAttribute("x2", `${ox + pd.x}`); ln.setAttribute("y2", `${oy + pd.y}`);
        }
      });
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => { alive = false; cancelAnimationFrame(raf); };
  }, [model, size.w, size.h]);

  if (!graph) return <div className="vault-hint">summoning the constellation…</div>;
  if (!graph.nodes.length)
    return <div className="vault-hint">No notes yet — the constellation grows as you file things.</div>;

  const neighbors = hovered ? (model?.adj.get(hovered) ?? new Set<string>()) : null;
  const showLabels = graph.nodes.length <= 24;
  const cx = size.w / 2, cy = size.h / 2;

  return (
    <div className="vault-graph" ref={wrapRef}>
      <svg className="vault-constellation" width="100%" height="100%">
        <defs>
          <radialGradient id="vault-core-glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--accent, #8b7cf6)" stopOpacity="0.18" />
            <stop offset="55%" stopColor="var(--accent, #8b7cf6)" stopOpacity="0.05" />
            <stop offset="100%" stopColor="var(--accent, #8b7cf6)" stopOpacity="0" />
          </radialGradient>
        </defs>

        <circle cx={cx} cy={cy} r={Math.min(size.w, size.h) * 0.42} fill="url(#vault-core-glow)" />
        {[0, 1, 2].map((i) => (
          <circle key={i} cx={cx} cy={cy} r={Math.min(size.w, size.h) * 0.2 * (i + 1)} className="vault-ring" />
        ))}

        <g className="vault-edges">
          {graph.edges.map((e, i) => {
            const lit = hovered && (e.src === hovered || e.dst === hovered);
            return <line key={i} ref={(el) => { edgeRefs.current[i] = el; }}
                         className={`vault-edge ${lit ? "lit" : ""} ${hovered && !lit ? "dim" : ""}`} />;
          })}
        </g>

        <g className="vault-nodes">
          {graph.nodes.map((n, i) => {
            const isHov = hovered === n.id;
            const isNbr = !!neighbors?.has(n.id);
            const dim = hovered && !isHov && !isNbr;
            const r = isHov ? 4.5 : 2.6 + Math.min(2.6, n.degree * 0.5);
            const title = n.title.length > 22 ? n.title.slice(0, 21) + "…" : n.title;
            return (
              <g key={n.id} ref={(el) => { if (el) nodeRefs.current.set(n.id, el); }}
                 className={`vault-gnode ${isHov ? "hov" : ""} ${dim ? "dim" : ""}`}
                 style={{ animationDelay: `${Math.min(i * 22, 700)}ms` }}
                 onMouseEnter={() => setHovered(n.id)}
                 onMouseLeave={() => setHovered((h) => (h === n.id ? null : h))}
                 onClick={() => onOpen(n.id)}>
                <circle className="vault-gnode-bloom" r={12} />
                <circle className="vault-gnode-dot" r={r} />
                {(isHov || showLabels) && (
                  <text className="vault-glabel" y={r + 13} textAnchor="middle">{title}</text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
