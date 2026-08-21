// Tile spawn / close commands routed from outside the TileGrid (the
// TopToolbar's + menu) to the TileGrid that actually owns the layout.
//
// We do NOT lift the layout state itself out of TileGrid — too invasive.
// Instead the TilesProvider holds a controller-ref. TileGrid registers a
// controller on mount; callers go through `spawnTile` / `closeTile` on
// the context, which delegate to the registered controller.

import {
  createContext, useCallback, useContext, useMemo, useRef, useState,
  type ReactNode,
} from "react";

/** Per-tile id exposed to descendant tile components via React context.
 *  TileGrid wraps each rendered tile in a provider with its concrete id.
 *  Multi-instance components (BrowserTile, TerminalTile, CodeTile) read
 *  this to scope their state (pool slot, pending URL, etc.). */
export const TileIdContext = createContext<string>("");
export function useTileId(): string { return useContext(TileIdContext); }

export type SpawnPoint = { x: number; y: number };

export type TileInfo = {
  id: string;
  label: string;
  description: string;
  /** Connector that must be present for this tile to appear in the +
   *  menu. Undefined => always available. The TopToolbar filters by
   *  this against live connector status (e.g. SpotifyContext). */
  requires?: "spotify";
};

/** Kinds that spawn a NEW independent instance every time the + menu
 *  fires them (Chrome-tabs style). Picking "Terminal" twice → two
 *  separate shells. Singletons (chat, news, clock, etc.) are NOT in
 *  this set and re-focus the existing tile on second click.
 *
 *  "subagent" is also multi-instance but NOT in the + menu — Nova spawns
 *  them programmatically via the dispatch tool. */
export const MULTI_INSTANCE_KINDS = new Set<string>(["terminal", "code", "browser", "subagent", "mission", "weather", "usage", "image", "audio", "video", "plan"]);

let _instanceSeq = 0;
export function makeMultiInstanceId(kind: string): string {
  _instanceSeq += 1;
  return `${kind}_${Date.now().toString(36)}_${_instanceSeq.toString(36)}`;
}

/** Test whether an id corresponds to a multi-instance kind (e.g.
 *  "terminal_abc" → "terminal"). Returns the kind or null. */
export function multiInstanceKind(id: string): string | null {
  const idx = id.indexOf("_");
  if (idx < 0) return null;
  const kind = id.slice(0, idx);
  return MULTI_INSTANCE_KINDS.has(kind) ? kind : null;
}

export const TILE_REGISTRY: TileInfo[] = [
  { id: "chat",     label: "Chat",        description: "Talk to Nova" },
  { id: "code",     label: "Code",        description: "Edit code" },
  { id: "terminal", label: "Terminal",    description: "Run commands" },
  { id: "browser",  label: "Browser",     description: "Browse the web" },
  { id: "news",     label: "News",        description: "Top headlines" },
  { id: "stocks",   label: "Markets",     description: "Track markets" },
  { id: "clock",    label: "World Clock", description: "World times" },
  { id: "todo",     label: "Tasks",       description: "Track tasks" },
  { id: "notes",    label: "Notes",       description: "Jot notes" },
  { id: "vault",    label: "Vault",       description: "Second brain" },
  { id: "skills",   label: "Skills",      description: "Nova's skill set" },
  { id: "summary",  label: "Summary",     description: "Tile summaries" },
  { id: "research", label: "Research",     description: "Source-check" },
  { id: "chain",    label: "Chain",        description: "Command pipeline" },
  { id: "plan",     label: "Plan",         description: "Milestone plan" },
  { id: "image",    label: "Image",       description: "Generated images" },
  { id: "audio",    label: "Audio",       description: "Generated music" },
  { id: "weather",  label: "Weather",     description: "Local forecast" },
  { id: "usage",    label: "Usage",       description: "API usage" },
];

export type TilesController = {
  spawn: (id: string, point?: SpawnPoint) => void;
  close: (id: string) => void;
  /** Tidy the visible tiles back into the default arrangement. */
  arrange: () => void;
  /** Scroll/pan the canvas to a tile by id + briefly highlight it. */
  focus: (id: string) => void;
};

type Ctx = {
  registry: TileInfo[];
  registerController: (c: TilesController | null) => void;
  spawnTile: (id: string, point?: SpawnPoint) => void;
  /** Spawn with a pre-generated id (bypasses makeMultiInstanceId). Used by
   *  Nova's dispatch_subagent tool which must register the job before spawn. */
  spawnTileWithId: (generatedId: string, point?: SpawnPoint) => void;
  /** Spawn several tiles at once (e.g. a "morning setup"). */
  spawnTiles: (ids: string[]) => void;
  /** Re-tidy visible tiles into the default arrangement. */
  arrangeTiles: () => void;
  closeTile: (id: string) => void;
  /** Focus (scroll-to + highlight) a tile by id. Chat tile-links call this. */
  focusTile: (id: string) => void;
  /** True only if the tile is actually on the canvas — used to avoid dead
   *  links (a chat tile-link renders clickable only when its target exists). */
  isTileActive: (id: string) => boolean;
  /** Snapshot of the currently-visible tile ids. The desktop TileGrid owns the
   *  layout and publishes this; the mobile shell reads it to enumerate tiles for
   *  its Feed/Focus/Canvas modes (additive — desktop ignores it). */
  activeIds: string[];
  /** Internal: TileGrid publishes its current visible tile ids here. */
  publishActiveIds: (ids: string[]) => void;
};

const TilesCtx = createContext<Ctx | null>(null);

export function TilesProvider({ children }: { children: ReactNode }) {
  const ctrlRef = useRef<TilesController | null>(null);
  const [activeIds, setActiveIds] = useState<Set<string>>(() => new Set());

  const registerController = useCallback((c: TilesController | null) => {
    ctrlRef.current = c;
  }, []);

  const focusTile = useCallback((id: string) => {
    ctrlRef.current?.focus(id);
  }, []);
  const isTileActive = useCallback((id: string) => activeIds.has(id), [activeIds]);
  const activeIdList = useMemo(() => [...activeIds], [activeIds]);
  const publishActiveIds = useCallback((ids: string[]) => {
    setActiveIds((cur) => {
      if (cur.size === ids.length && ids.every((i) => cur.has(i))) return cur;
      return new Set(ids);
    });
  }, []);

  const spawnTile = useCallback((id: string, point?: SpawnPoint) => {
    // If the + menu fired a kind that's flagged as multi-instance, we
    // ALWAYS spawn a fresh tile with a unique id. Singletons keep the
    // exact id they were registered under.
    const effective = MULTI_INSTANCE_KINDS.has(id) ? makeMultiInstanceId(id) : id;
    ctrlRef.current?.spawn(effective, point);
  }, []);
  const spawnTileWithId = useCallback((generatedId: string, point?: SpawnPoint) => {
    ctrlRef.current?.spawn(generatedId, point);
  }, []);
  const spawnTiles = useCallback((ids: string[]) => {
    for (const id of ids) {
      const effective = MULTI_INSTANCE_KINDS.has(id) ? makeMultiInstanceId(id) : id;
      ctrlRef.current?.spawn(effective);
    }
  }, []);
  const arrangeTiles = useCallback(() => {
    ctrlRef.current?.arrange();
  }, []);
  const closeTile = useCallback((id: string) => {
    ctrlRef.current?.close(id);
  }, []);

  const value = useMemo<Ctx>(() => ({
    registry: TILE_REGISTRY,
    registerController, spawnTile, spawnTileWithId, spawnTiles, arrangeTiles, closeTile,
    focusTile, isTileActive, activeIds: activeIdList, publishActiveIds,
  }), [registerController, spawnTile, spawnTileWithId, spawnTiles, arrangeTiles, closeTile,
       focusTile, isTileActive, activeIdList, publishActiveIds]);

  return <TilesCtx.Provider value={value}>{children}</TilesCtx.Provider>;
}

export function useTiles(): Ctx {
  const v = useContext(TilesCtx);
  if (!v) throw new Error("useTiles called outside TilesProvider");
  return v;
}
