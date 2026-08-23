// Free-canvas tile layout (no react-grid-layout).
//
// Each tile has {x, y, w, h, z} in CANVAS coordinates and renders as an
// absolutely-positioned <div> at that x/y, sized w x h. Tiles place freely
// — drag a tile into open space and it stays. No reflow, no auto-packing,
// no collision avoidance. Overlap is allowed; the most-recently-touched
// tile rises in z-index.
//
// Critical coordinate-space contract:
//   The outer Dashboard wrapper has CSS  transform: translate(panX, panY) scale(zoom)
//   with transform-origin: 0 0.
//   Children inside it (tile divs) live in PRE-transform "canvas space".
//   Cursor deltas during drag/resize are in screen space — divide by zoom
//   before applying to canvas coordinates so the tile tracks the cursor 1:1
//   at any zoom level.
//
// Spawn / close: the TopToolbar talks to us through TilesContext, which
// registers our controller. spawn(id, point) opens (or focuses) the tile
// centred on the given canvas-space point; close(id) hides it.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ChatTile from "./tiles/ChatTile";
import ClockTile from "./tiles/ClockTile";
import NotesTile from "./tiles/NotesTile";
import NewsTile from "./tiles/NewsTile";
import TodoTile from "./tiles/TodoTile";
import StockTile from "./tiles/StockTile";
import BrowserTile from "./tiles/BrowserTile";
import FileTile from "./tiles/FileTile";
// Lazy on purpose — it carries the bundled Monaco engine. See CodeTileLazy.
import CodeTile from "./tiles/CodeTileLazy";
import TerminalTile from "./tiles/TerminalTile";
import SubAgentTile from "./tiles/SubAgentTile";
import MissionTile from "./tiles/MissionTile";
import WeatherTile from "./tiles/WeatherTile";
import UsageTile from "./tiles/UsageTile";
import DocsTile from "./tiles/DocsTile";
import SummaryTile from "./tiles/SummaryTile";
import ArtifactTile from "./tiles/ArtifactTile";
import ResearchTile from "./tiles/ResearchTile";
import ChainTile from "./tiles/ChainTile";
import PlanTile from "./tiles/PlanTile";
import VaultTile from "./tiles/VaultTile";
import SkillsTile from "./tiles/SkillsTile";
import ConsoleTile from "./tiles/ConsoleTile";
// (Browser is now multi-instance and self-contained; no BrowserContext usage here.)
import { useCanvas } from "../state/CanvasContext";
import { useTiles, type SpawnPoint, multiInstanceKind, TileIdContext } from "../state/TilesContext";
import { useFiles } from "../state/FilesContext";

const LAYOUT_KEY = "infinity.layout.v4";
const CLOSED_KEY = "infinity.closedTiles.v1";

export type TileRect = { x: number; y: number; w: number; h: number; z: number };
type LayoutMap = Record<string, TileRect>;

type TileDef = {
  i: string;
  component: () => ReactNode;
  defaultRect: TileRect;
  minW?: number;
  minH?: number;
};

const TILE_DEFS: TileDef[] = [
  { i: "chat",    component: () => <ChatTile />,
    defaultRect: { x:   0, y:   0, w: 640, h: 480, z: 5 }, minW: 380, minH: 280 },
  { i: "clock",   component: () => <ClockTile />,
    defaultRect: { x: 680, y:   0, w: 360, h: 240, z: 1 }, minW: 260, minH: 200 },
  { i: "stocks",  component: () => <StockTile />,
    defaultRect: { x: 680, y: 260, w: 360, h: 460, z: 1 }, minW: 300, minH: 320 },
  { i: "news",    component: () => <NewsTile />,
    defaultRect: { x:   0, y: 500, w: 640, h: 360, z: 1 }, minW: 320, minH: 240 },
  { i: "todo",    component: () => <TodoTile />,
    defaultRect: { x: 680, y: 740, w: 360, h: 220, z: 1 }, minW: 260, minH: 180 },
  { i: "notes",   component: () => <NotesTile />,
    defaultRect: { x:   0, y: 880, w: 640, h: 220, z: 1 }, minW: 280, minH: 140 },
  { i: "docs",    component: () => <DocsTile />,
    defaultRect: { x: 160, y: 120, w: 460, h: 420, z: 11 }, minW: 300, minH: 260 },
  { i: "summary", component: () => <SummaryTile />,
    defaultRect: { x: 200, y: 160, w: 480, h: 440, z: 12 }, minW: 300, minH: 240 },
  { i: "research", component: () => <ResearchTile />,
    defaultRect: { x: 220, y: 180, w: 520, h: 520, z: 12 }, minW: 320, minH: 280 },
  { i: "chain", component: () => <ChainTile />,
    defaultRect: { x: 180, y: 140, w: 420, h: 360, z: 14 }, minW: 280, minH: 200 },
  { i: "browser", component: () => <BrowserTile />,
    defaultRect: { x:   0, y:1120, w:1040, h: 480, z:10 }, minW: 420, minH: 280 },
  { i: "code", component: () => <CodeTile />,
    defaultRect: { x: 160, y: 100, w: 980, h: 620, z:10 }, minW: 480, minH: 320 },
  { i: "terminal", component: () => <TerminalTile />,
    defaultRect: { x: 180, y: 160, w: 760, h: 420, z:10 }, minW: 360, minH: 220 },
  { i: "vault", component: () => <VaultTile />,
    defaultRect: { x: 120, y: 120, w: 720, h: 560, z:11 }, minW: 420, minH: 340 },
  { i: "skills", component: () => <SkillsTile />,
    defaultRect: { x: 200, y: 160, w: 460, h: 480, z:11 }, minW: 320, minH: 280 },
  { i: "console", component: () => <ConsoleTile />,
    defaultRect: { x: 240, y: 140, w: 560, h: 620, z:12 }, minW: 380, minH: 320 },
];

const PERMANENT_IDS = new Set(["chat", "clock", "stocks", "news", "todo", "notes"]);
// Singleton-but-opt-in: one instance, must be spawned, focus-on-reopen.
const EPHEMERAL_OPENABLE = new Set(["docs", "summary", "research", "chain", "vault", "skills", "console"]);

// Multi-instance kinds (Chrome-tabs style): every + menu pick is a NEW
// tile with a unique id and its own state. Keyed by KIND ("terminal",
// "code", "browser", "subagent") — the actual tile ids are `${kind}_<ts>_<seq>`.
// "subagent" is NOT in the + menu — Nova spawns it programmatically.
const MULTI_INSTANCE_DEFAULTS: Record<string, { component: () => ReactNode; defaultRect: TileRect; minW: number; minH: number }> = {
  terminal: { component: () => <TerminalTile />,
              defaultRect: { x: 180, y: 160, w: 760, h: 420, z: 10 }, minW: 360, minH: 220 },
  code:     { component: () => <CodeTile />,
              defaultRect: { x: 160, y: 100, w: 980, h: 620, z: 10 }, minW: 480, minH: 320 },
  browser:  { component: () => <BrowserTile />,
              defaultRect: { x:   0, y:1120, w:1040, h: 480, z: 10 }, minW: 420, minH: 280 },
  subagent: { component: () => <SubAgentTile />,
              defaultRect: { x: 200, y: 200, w: 600, h: 400, z: 12 }, minW: 360, minH: 260 },
  mission:  { component: () => <MissionTile />,
              defaultRect: { x: 160, y: 160, w: 680, h: 560, z: 13 }, minW: 420, minH: 360 },
  weather:  { component: () => <WeatherTile />,
              defaultRect: { x: 220, y: 180, w: 340, h: 440, z: 10 }, minW: 280, minH: 320 },
  usage:    { component: () => <UsageTile />,
              defaultRect: { x: 240, y: 200, w: 320, h: 360, z: 10 }, minW: 260, minH: 240 },
  image:    { component: () => <ArtifactTile />,
              defaultRect: { x: 220, y: 180, w: 480, h: 520, z: 12 }, minW: 280, minH: 300 },
  audio:    { component: () => <ArtifactTile />,
              defaultRect: { x: 240, y: 200, w: 420, h: 200, z: 12 }, minW: 280, minH: 150 },
  video:    { component: () => <ArtifactTile />,
              defaultRect: { x: 240, y: 200, w: 560, h: 420, z: 12 }, minW: 320, minH: 240 },
  plan:     { component: () => <PlanTile />,
              defaultRect: { x: 200, y: 150, w: 560, h: 600, z: 13 }, minW: 340, minH: 320 },
};

const TILE_BY_ID = new Map(TILE_DEFS.map((t) => [t.i, t]));

// Cheap no-overlap placement (NOT bin-packing): scan a coarse grid from the
// top-left of the existing cluster and return the first slot whose box (plus a
// gap) intersects nothing. Bounded — the search region extends past the cluster
// by one tile + margin, so it ALWAYS returns and never loops; if the area is
// genuinely full it falls back to a small cascade offset. Canvas-space coords.
function findOpenSlot(
  w: number, h: number, occupied: TileRect[],
  origin?: { x: number; y: number },
): { x: number; y: number } {
  const GAP = 16;
  const STEP = 36;
  const hits = (x: number, y: number) =>
    occupied.some((r) =>
      x < r.x + r.w + GAP && x + w + GAP > r.x &&
      y < r.y + r.h + GAP && y + h + GAP > r.y);
  let ox = 0, oy = 0, mx = w + 600, my = h + 600;
  if (origin) {
    // Scan outward from the intended drop point (e.g. the viewport centre the +
    // menu asked for) so a new tile lands NEAR where you're looking but never on
    // top of another tile.
    ox = origin.x - w / 2;
    oy = origin.y - h / 2;
    mx = ox + w + 900;
    my = oy + h + 900;
  } else if (occupied.length) {
    ox = Math.min(...occupied.map((r) => r.x));
    oy = Math.min(...occupied.map((r) => r.y));
    mx = Math.max(...occupied.map((r) => r.x + r.w)) + w + GAP;
    my = Math.max(...occupied.map((r) => r.y + r.h)) + h + GAP;
  }
  for (let y = oy; y <= my; y += STEP) {
    for (let x = ox; x <= mx; x += STEP) {
      if (!hits(x, y)) return { x, y };
    }
  }
  const n = occupied.length;
  return { x: ox + (n % 8) * 30, y: oy + (n % 8) * 30 };
}

function defaultLayoutMap(): LayoutMap {
  const m: LayoutMap = {};
  for (const t of TILE_DEFS) m[t.i] = { ...t.defaultRect };
  return m;
}

function loadLayout(): LayoutMap {
  const def = defaultLayoutMap();
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (!raw) return def;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return def;
    return { ...def, ...parsed };
  } catch { return def; }
}

function loadClosed(): Set<string> {
  try {
    const raw = localStorage.getItem(CLOSED_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((x): x is string => typeof x === "string"));
  } catch { return new Set(); }
}

// Selectors whose mousedown should NEVER start a tile drag (interactive
// content + nested scroll surfaces + the resize handle + the tile-close
// glyph + the draw capture layer).
const NO_DRAG_SELECTOR =
  "input, textarea, button, a, select, label, .no-drag, " +
  ".chat-scroll, .md-host, .chat-msg-text, .tnotes-area, " +
  ".tnews-list, .tstock-list, .ttodo-list, .tclock-list, " +
  ".tclock-results, .tstock-search-pop, .codeblock, " +
  ".tbrowser-body, " +
  ".free-resize-handle, .free-tile-close, .canvas-draw-capture";

export default function TileGrid() {
  const { zoom, setPan, canvasRef } = useCanvas();
  /* no useBrowser here — each BrowserTile manages its own pool slot + URL. */
  const { registerController, publishActiveIds } = useTiles();
  const { files, closeFile } = useFiles();

  const [layout, setLayout] = useState<LayoutMap>(() => loadLayout());
  const [closedIds, setClosedIds] = useState<Set<string>>(() => loadClosed());
  // Fresh layout/zoom for the focus() controller method (registered once, so it
  // must read live values via refs rather than a stale closure).
  const layoutRef = useRef<LayoutMap>(layout);
  layoutRef.current = layout;
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const [extraOpen, setExtraOpen] = useState<Set<string>>(() => new Set());
  const [pulseId, setPulseId] = useState<{ id: string; n: number } | null>(null);
  // No-overlap spawn: live set of on-canvas ids + ids spawned in the current
  // burst (so a chain's tiles don't stack on each other before re-render).
  const activeIdsRef = useRef<Set<string>>(new Set());
  const recentSpawnRef = useRef<Set<string>>(new Set());

  // Monotonic z counter — every interact-with-tile raises it.
  const zCounterRef = useRef<number>(100);

  // Currently-visible tile set: built-in tiles + every open file tile
  // + every multi-instance tile (terminal_*, code_*, browser_*).
  const activeTiles = useMemo<TileDef[]>(() => {
    const out: TileDef[] = TILE_DEFS.filter((t) => {
      if (t.i === "browser") return false;        // legacy singleton path retired
      if (t.i === "code")    return false;        // moved to multi-instance below
      if (t.i === "terminal") return false;       // moved to multi-instance below
      // EVERY ephemeral-openable tile (docs, summary, research, chain, …) is
      // governed by ONE mechanism: extraOpen. Spawn adds it, close
      // removes it. This is the root-cause fix for "research/chain/vision can't
      // be closed" — they were in TILE_DEFS so their visibility fell through to
      // `!closedIds`, but close() routes ephemerals through extraOpen and
      // returned early, so they never hid. Now the two agree.
      if (EPHEMERAL_OPENABLE.has(t.i)) return extraOpen.has(t.i);
      return !closedIds.has(t.i);                 // PERMANENT_IDS only
    });
    for (const f of files) {
      out.push({
        i: f.id,
        component: () => <FileTile file={f} />,
        defaultRect: { x: 0, y: 0, w: 480, h: 360, z: 5 },
        minW: 240, minH: 180,
      });
    }
    // Multi-instance: each open tile-id (e.g. "terminal_abc") gets its
    // own entry. The component for the kind is shared but each render
    // is an independent React subtree → independent state.
    for (const id of extraOpen) {
      const kind = multiInstanceKind(id);
      if (!kind) continue;
      const tpl = MULTI_INSTANCE_DEFAULTS[kind];
      if (!tpl) continue;
      out.push({ i: id, component: tpl.component, defaultRect: tpl.defaultRect, minW: tpl.minW, minH: tpl.minH });
    }
    return out;
  }, [closedIds, files, extraOpen]);

  // Keep the live active-id set fresh for no-overlap spawn placement.
  activeIdsRef.current = new Set(activeTiles.map((t) => t.i));

  // Backfill: any active tile missing from layout gets its default rect.
  useEffect(() => {
    setLayout((cur) => {
      const next = { ...cur };
      let changed = false;
      for (const t of activeTiles) {
        if (!(t.i in next)) {
          next[t.i] = { ...t.defaultRect };
          changed = true;
        }
      }
      return changed ? next : cur;
    });
  }, [activeTiles]);

  // Debounced persistence.
  useEffect(() => {
    const t = window.setTimeout(() => {
      try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch { /* ignore */ }
    }, 250);
    return () => window.clearTimeout(t);
  }, [layout]);
  useEffect(() => {
    try { localStorage.setItem(CLOSED_KEY, JSON.stringify([...closedIds])); } catch { /* ignore */ }
  }, [closedIds]);

  const bringToFront = (id: string) => {
    zCounterRef.current += 1;
    const nextZ = zCounterRef.current;
    setLayout((prev) => {
      const cur = prev[id];
      if (!cur || cur.z === nextZ) return prev;
      return { ...prev, [id]: { ...cur, z: nextZ } };
    });
  };

  const updateRect = (id: string, patch: Partial<TileRect>) => {
    setLayout((prev) => {
      const cur = prev[id];
      if (!cur) return prev;
      return { ...prev, [id]: { ...cur, ...patch } };
    });
  };

  // Wire the controller so the TopToolbar's + menu can drive us.
  useEffect(() => {
    const isFileId = (id: string) => id.startsWith("file_");

    const spawn = (id: string, point?: SpawnPoint) => {
      const builtIn = TILE_BY_ID.get(id);
      const fileTile = isFileId(id);

      if (fileTile) {
        // File tiles are already "active" once they're in FilesContext.files.
        // Nothing extra to flip; just position + bring to front below.
      } else if (multiInstanceKind(id)) {
        // Already-unique id (TilesContext.spawnTile minted it). Just open
        // it. Each spawn = new tile = new instance — no dedup.
        setExtraOpen((cur) => {
          if (cur.has(id)) return cur;
          const next = new Set(cur);
          next.add(id);
          return next;
        });
      } else if (EPHEMERAL_OPENABLE.has(id)) {
        // Singleton opt-in (docs, research, …). Re-spawn = focus existing.
        setExtraOpen((cur) => {
          if (cur.has(id)) return cur;
          const next = new Set(cur);
          next.add(id);
          return next;
        });
      } else if (PERMANENT_IDS.has(id)) {
        if (closedIds.has(id)) {
          setClosedIds((cur) => {
            const next = new Set(cur);
            next.delete(id);
            return next;
          });
        }
      } else if (!builtIn) {
        return;
      }

      // Position at the supplied point. For multi-instance tiles each
      // new spawn gets a small offset stagger so successive opens don't
      // exactly overlap.
      const kind = multiInstanceKind(id);
      const multiTpl = kind ? MULTI_INSTANCE_DEFAULTS[kind] : null;
      const fallback = multiTpl?.defaultRect
        ?? builtIn?.defaultRect
        ?? { x: 0, y: 0, w: 480, h: 360, z: 5 };
      const stagger = kind
        ? (Array.from(extraOpen).filter((tid) => multiInstanceKind(tid) === kind).length) * 28
        : 0;
      // A tile that isn't already on the canvas is "freshly appearing" — it goes
      // through no-overlap placement REGARDLESS of whether a point was supplied
      // (the + menu passes the viewport centre, which used to bypass the
      // open-slot search and overlap whatever was there — the vision-overlap
      // bug). The point now only BIASES the search origin.
      const freshlyAppearing = !activeIdsRef.current.has(id);
      if (freshlyAppearing) {
        recentSpawnRef.current.add(id);
        window.setTimeout(() => recentSpawnRef.current.delete(id), 2000);
      }
      setLayout((prev) => {
        const cur = prev[id] ?? fallback;
        const w = cur.w, h = cur.h, z = cur.z;
        let placed: TileRect;
        if (freshlyAppearing) {
          // Drop a newly-appearing tile into an empty slot — NEVER overlapping.
          // Occupied = on-canvas tiles + this burst's already-placed siblings
          // (read from live `prev`, so a chain's tiles each find their own spot).
          // Bias the search toward the requested point (viewport centre) so it
          // still lands near where the user is looking.
          const occupied = Object.entries(prev)
            .filter(([tid]) => tid !== id && (activeIdsRef.current.has(tid) || recentSpawnRef.current.has(tid)))
            .map(([, r]) => r);
          const slot = findOpenSlot(w, h, occupied, point ? { x: point.x + stagger, y: point.y + stagger } : undefined);
          placed = { x: slot.x, y: slot.y, w, h, z };
        } else if (point) {
          // Already on canvas + an explicit point → move it there (rare).
          placed = { x: point.x - w / 2 + stagger, y: point.y - h / 2 + stagger, w, h, z };
        } else {
          placed = cur;   // already on canvas, no point → keep position (focus)
        }
        return { ...prev, [id]: placed };
      });
      bringToFront(id);
      setPulseId({ id, n: Date.now() });
    };

    const close = (id: string) => {
      if (isFileId(id)) {
        closeFile(id);
        setLayout((prev) => {
          if (!(id in prev)) return prev;
          const next = { ...prev };
          delete next[id];
          return next;
        });
        return;
      }
      // Multi-instance OR singleton-opt-in — both live in extraOpen.
      if (multiInstanceKind(id) || EPHEMERAL_OPENABLE.has(id)) {
        setExtraOpen((cur) => {
          if (!cur.has(id)) return cur;
          const next = new Set(cur);
          next.delete(id);
          return next;
        });
        // Multi-instance tiles also drop their layout entry to avoid
        // localStorage bloat after the user closes a handful of them.
        if (multiInstanceKind(id)) {
          setLayout((prev) => {
            if (!(id in prev)) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
          });
        }
        return;
      }
      if (!PERMANENT_IDS.has(id)) return;
      setClosedIds((cur) => {
        const next = new Set(cur);
        next.add(id);
        return next;
      });
    };

    // Tidy: snap every currently-visible tile back to its default rect.
    const arrange = () => {
      setLayout((prev) => {
        const next = { ...prev };
        for (const t of activeTiles) {
          const kind = multiInstanceKind(t.i);
          const def = kind ? MULTI_INSTANCE_DEFAULTS[kind]?.defaultRect : TILE_BY_ID.get(t.i)?.defaultRect;
          const fallback = def ?? t.defaultRect;
          next[t.i] = { ...fallback };
        }
        return next;
      });
    };

    // Focus: pan the canvas so the tile is centered, raise it, and pulse it.
    // Only acts on a tile that actually has a layout rect (no dead focus).
    const focus = (id: string) => {
      const rect = layoutRef.current[id];
      const el = canvasRef.current;
      if (!rect || !el) return;
      const z = zoomRef.current;
      const r = el.getBoundingClientRect();
      const cx = rect.x + rect.w / 2;
      const cy = rect.y + rect.h / 2;
      // Re-pan to center every time (fresh object → always re-applies, even if
      // the tile was last-focused). Absolute target, independent of current pan.
      setPan({ x: r.width / 2 - cx * z, y: r.height / 2 - cy * z });
      bringToFront(id);
      // Re-pulse every time: clear then re-set on the next frame so the CSS
      // animation actually RESTARTS on repeat clicks (otherwise the class never
      // toggles and the highlight only plays the first time).
      setPulseId(null);
      requestAnimationFrame(() => setPulseId({ id, n: Date.now() }));
    };

    registerController({ spawn, close, arrange, focus });
    return () => registerController(null);
  }, [registerController, closedIds, closeFile, extraOpen, activeTiles, setPan, canvasRef]);

  // Publish the visible tile-id set so chat tile-links render only for tiles
  // that actually exist (no dead links).
  useEffect(() => {
    publishActiveIds(activeTiles.map((t) => t.i));
  }, [activeTiles, publishActiveIds]);

  // Clear pulse class after the animation duration.
  useEffect(() => {
    if (!pulseId) return;
    const h = window.setTimeout(() => setPulseId(null), 750);
    return () => window.clearTimeout(h);
  }, [pulseId]);

  const onCloseTile = (id: string) => {
    if (id.startsWith("file_")) {
      closeFile(id);
      setLayout((prev) => {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
      return;
    }
    if (multiInstanceKind(id) || EPHEMERAL_OPENABLE.has(id)) {
      setExtraOpen((cur) => {
        const next = new Set(cur);
        next.delete(id);
        return next;
      });
      if (multiInstanceKind(id)) {
        setLayout((prev) => {
          if (!(id in prev)) return prev;
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }
      return;
    }
    setClosedIds((cur) => {
      const next = new Set(cur);
      next.add(id);
      return next;
    });
  };

  return (
    <div className="free-canvas">
      {activeTiles.map((t) => {
        const rect = layout[t.i] ?? t.defaultRect;
        const pulsing = pulseId?.id === t.i;
        return (
          <TileIdContext.Provider value={t.i} key={t.i}>
            <TileFrame
              id={t.i}
              rect={rect}
              zoom={zoom}
              minW={t.minW ?? 200}
              minH={t.minH ?? 140}
              pulsing={pulsing}
              onActivate={() => bringToFront(t.i)}
              onMove={(x, y) => updateRect(t.i, { x, y })}
              onResize={(w, h) => updateRect(t.i, { w, h })}
              onClose={() => onCloseTile(t.i)}
            >
              {t.component()}
            </TileFrame>
          </TileIdContext.Provider>
        );
      })}
    </div>
  );
}

type TileFrameProps = {
  id: string;
  rect: TileRect;
  zoom: number;
  minW: number;
  minH: number;
  pulsing: boolean;
  onActivate: () => void;
  onMove: (x: number, y: number) => void;
  onResize: (w: number, h: number) => void;
  onClose: () => void;
  children: ReactNode;
};

function TileFrame({ id, rect, zoom, minW, minH, pulsing, onActivate, onMove, onResize, onClose, children }: TileFrameProps) {
  // One-shot spawn-in: mount with `.spawning` (opacity 0, slight scale-down),
  // drop it next frame → the CSS transition glides it in. A transition (not an
  // animation) so the `.pulse` animation never replays it on focus.
  const [spawning, setSpawning] = useState(true);
  useEffect(() => {
    const r = requestAnimationFrame(() => setSpawning(false));
    return () => cancelAnimationFrame(r);
  }, []);

  // Ref to the tile's own DOM node so a DRAG can reposition it directly without
  // re-rendering. (Committing every mousemove to React state re-renders the
  // WHOLE tile set — `activeTiles.map` re-runs every tile's component, heavy
  // ones included — which is the post-migration "drag fluidity gone" jank.)
  const frameRef = useRef<HTMLDivElement>(null);

  // POINTER events (not mouse-only): one handler covers mouse, touch, and pen,
  // so tiles drag/resize on the phone exactly like the desktop mouse — the
  // mobile fix for "tiles can't be dragged by touch". setPointerCapture keeps
  // the gesture glued to this tile even if the finger leaves it.
  const onTilePointerDown = (e: React.PointerEvent) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    onActivate();
    const target = e.target as Element;
    // SHARED DEFAULT: a tile drags ONLY by its title bar (.tile-head). Content —
    // the vision graph's orbit/pan, scroll areas, inputs, canvases — keeps its
    // own interactions and never moves the tile.
    if (!target.closest(".tile-head")) return;
    if (target.closest(NO_DRAG_SELECTOR)) return;
    e.preventDefault();
    const pid = e.pointerId;
    try { frameRef.current?.setPointerCapture(pid); } catch { /* ignore */ }

    const startX = e.clientX, startY = e.clientY;
    const origX = rect.x, origY = rect.y;
    let lastX = origX, lastY = origY;

    const onPointerMove = (em: PointerEvent) => {
      if (em.pointerId !== pid) return;
      lastX = origX + (em.clientX - startX) / zoom;
      lastY = origY + (em.clientY - startY) / zoom;
      // Move ONLY this node's DOM — no setState, so no other tile re-renders.
      const el = frameRef.current;
      if (el) { el.style.left = `${lastX}px`; el.style.top = `${lastY}px`; }
    };
    const onPointerUp = (em: PointerEvent) => {
      if (em.pointerId !== pid) return;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      onMove(lastX, lastY);   // commit final position to React state ONCE
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  };

  const onResizePointerDown = (e: React.PointerEvent) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    onActivate();
    const pid = e.pointerId;
    try { e.currentTarget.setPointerCapture(pid); } catch { /* ignore */ }

    const startX = e.clientX, startY = e.clientY;
    const origW = rect.w, origH = rect.h;

    const onPointerMove = (em: PointerEvent) => {
      if (em.pointerId !== pid) return;
      const dw = (em.clientX - startX) / zoom;
      const dh = (em.clientY - startY) / zoom;
      onResize(Math.max(minW, origW + dw), Math.max(minH, origH + dh));
    };
    const onPointerUp = (em: PointerEvent) => {
      if (em.pointerId !== pid) return;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  };

  return (
    <div
      ref={frameRef}
      className={`free-tile ${pulsing ? "pulse" : ""} ${spawning ? "spawning" : ""}`}
      // Inert hook for the mobile shell's tap-to-deep-dive (read by MobileShell's
      // canvas gesture layer). No effect on desktop rendering or behavior.
      data-tile-id={id}
      style={{
        position: "absolute",
        left: rect.x,
        top: rect.y,
        width: rect.w,
        height: rect.h,
        zIndex: rect.z,
      }}
      onPointerDown={onTilePointerDown}
    >
      <button
        type="button"
        className="free-tile-close"
        onClick={(e) => { e.stopPropagation(); onClose(); }}
        title={`Close ${id}`}
        aria-label={`Close ${id}`}
      >
        <svg viewBox="0 0 12 12" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1.4">
          <path d="M2.5 2.5 L9.5 9.5 M9.5 2.5 L2.5 9.5" strokeLinecap="round" />
        </svg>
      </button>
      {children}
      <div
        className="free-resize-handle"
        onPointerDown={onResizePointerDown}
        title="Resize"
        aria-label="Resize"
      />
    </div>
  );
}
