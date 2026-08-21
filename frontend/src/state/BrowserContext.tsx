// Each BrowserTile is an independent instance with its own state. Browsing
// is now SERVER-SIDE (see backend/app/browse.py) — no Tauri child window,
// no window pool, no iframe. The legacy singleton model is gone.
//
// `openInBrowser(url)` is preserved as the entry point external tiles
// (NewsTile headline clicks, etc.) call. Internally
// it now SPAWNS a new BrowserTile with the URL pre-loaded:
//   1. mint a multi-instance tile id (e.g. `browser_<ts>_<n>`)
//   2. stash the URL in the module-level pending-URL map keyed by id
//   3. invoke TilesContext.spawnTile(id, viewportCenter)
// On mount, the new BrowserTile reads its initial URL from that map
// (via consumePendingBrowserUrl) and navigates to it once.
//
// `close()` is kept for back-compat but is now a no-op — each tile owns
// its own close path via the tile-frame X button.

import {
  createContext, useCallback, useContext, useMemo,
  type ReactNode,
} from "react";
import { useTiles, makeMultiInstanceId } from "./TilesContext";
import { useCanvas } from "./CanvasContext";

// Tile-id → initial URL. Set when openInBrowser fires; consumed once on
// the new tile's first mount.
const PENDING: Map<string, string> = new Map();

export function consumePendingBrowserUrl(tileId: string): string | null {
  const v = PENDING.get(tileId);
  if (v === undefined) return null;
  PENDING.delete(tileId);
  return v;
}

// ── BrowserContext (thin facade) ──────────────────────────────────

type Ctx = {
  /** External entry — spawns a new BrowserTile pre-loaded with `url`.
   *  Returns the new tile's id so callers can focus it. */
  openInBrowser: (url: string) => string;
  /** No-op kept for back-compat. Each tile closes via its own X button. */
  close: () => void;
};

const BrowserCtx = createContext<Ctx | null>(null);

export function BrowserProvider({ children }: { children: ReactNode }) {
  const { spawnTile } = useTiles();
  const { viewportCenter } = useCanvas();

  const openInBrowser = useCallback((url: string): string => {
    const id = makeMultiInstanceId("browser");
    PENDING.set(id, url);
    // spawnTile here will NOT re-mint another id because the id is
    // already kind-prefixed-and-suffixed; only bare "browser" triggers
    // re-mint inside TilesContext.
    spawnTile(id, viewportCenter());
    return id;
  }, [spawnTile, viewportCenter]);

  const close = useCallback(() => { /* no-op in multi-instance world */ }, []);

  const value = useMemo<Ctx>(() => ({ openInBrowser, close }), [openInBrowser, close]);
  return <BrowserCtx.Provider value={value}>{children}</BrowserCtx.Provider>;
}

export function useBrowser(): Ctx {
  const v = useContext(BrowserCtx);
  if (!v) throw new Error("useBrowser called outside BrowserProvider");
  return v;
}
