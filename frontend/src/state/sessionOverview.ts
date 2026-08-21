// Session-overview builder — gathers the live state of all tiles and ships it
// to the backend's ephemeral, single-slot session-overview RAG index (which
// chat retrieves from each turn for grounding). Reuses the SAME tile-gather
// path /summarize and the daily greeting use (tileRegistry.gatherTileData), so
// there's no new gather infra. Built at launch + on /refresh; each build
// OVERWRITES the prior slot (the backend replaces, never stacks).

import { gatherTileData } from "./tileRegistry";
import { postSessionOverview } from "../api";

// Every live data tile worth grounding on. Empty/unavailable ones return a
// marker from gatherTileData and are simply not useful context — no harm.
const OVERVIEW_TILE_IDS = ["weather", "portfolio", "news", "tasks", "clock", "notes", "spotify"];

let _building = false;

/** Gather all tile data → backend session-overview (overwrite). Returns the
 *  chunk count (0 on any failure — chat then runs ungrounded, never crashes). */
export async function refreshSessionOverview(): Promise<number> {
  if (_building) return 0;          // de-dupe concurrent builds (launch + /refresh race)
  _building = true;
  try {
    let text = "";
    try {
      const blocks = await gatherTileData(OVERVIEW_TILE_IDS);
      // Keep only blocks that carry real content (drop pure "unavailable" markers).
      text = blocks
        .filter((b) => b && !/\bunavailable\b/i.test(b) && b.trim().length > 0)
        .join("\n\n");
    } catch {
      text = "";
    }
    const stamp = `[session captured ${new Date().toLocaleString()}]`;
    const { chunks } = await postSessionOverview(text ? `${stamp}\n\n${text}` : "");
    return chunks;
  } finally {
    _building = false;
  }
}
