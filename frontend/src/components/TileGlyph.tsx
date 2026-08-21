// Per-tile-type icon — ported from the Claude Design "Infinity Mobile" prototype
// (its `I` icon set + TILES glyph map). The real tiles historically used a
// text-only `tile-title`; this adds the design's line-icon next to that title so
// each tile type reads at a glance. Used on BOTH desktop and mobile tile heads
// (intentional, additive — the title text stays) AND by the mobile Focus/Feed
// chrome (one source of truth for tile glyphs).
//
// Rendered as a child of the title span; <TileGlyph/> with no prop reads the
// concrete tile id from TileIdContext and derives the type (so "image_abc" →
// image, "file_x" → file). Pass `type` to force one.

import { type ReactNode } from "react";
import { multiInstanceKind, useTileId } from "../state/TilesContext";

// Void-violet line-icons. The overlapping types (chat, plan=spark, research=doc,
// image, weather=cloud, stocks=chart, news, tasks=check, notes) are the design
// prototype's exact glyphs; the rest are clean matching line-icons in the same
// 16×16 / stroke style.
const GLYPHS: Record<string, ReactNode> = {
  chat: <path d="M2.5 4 a1.5 1.5 0 0 1 1.5-1.5 h8 a1.5 1.5 0 0 1 1.5 1.5 v5 a1.5 1.5 0 0 1-1.5 1.5 H7 l-3 2.5 v-2.5 H4 a1.5 1.5 0 0 1-1.5-1.5 Z" strokeLinejoin="round" />,
  plan: <path d="M8 2 L9 6.5 L13.5 8 L9 9.5 L8 14 L7 9.5 L2.5 8 L7 6.5 Z" strokeLinejoin="round" />,
  research: <g><path d="M4 2.5 h5 L12 5.5 V13 a0.5 0.5 0 0 1-0.5 0.5 H4 a0.5 0.5 0 0 1-0.5-0.5 V3 a0.5 0.5 0 0 1 0.5-0.5 Z" /><path d="M9 2.5 V5.5 H12" /></g>,
  image: <g><rect x="2" y="3" width="12" height="10" rx="1.5" /><circle cx="5.5" cy="6.5" r="1.2" /><path d="M3 12 L7 8 L10 11 L11.5 9.5 L13 11" /></g>,
  audio: <g><path d="M5 10 V5 L12 3.5 V8.5" /><circle cx="4" cy="10.5" r="1.6" /><circle cx="11" cy="9" r="1.6" /></g>,
  video: <g><rect x="2" y="3.5" width="9" height="9" rx="1.2" /><path d="M11 7 L14 5 V11 L11 9 Z" /></g>,
  weather: <path d="M4.5 11 a2.5 2.5 0 0 1 0-5 a3 3 0 0 1 5.8-1 a2.2 2.2 0 0 1 0.7 6 Z" strokeLinejoin="round" />,
  stocks: <path d="M2 13 L6 8 L9 10 L14 3" strokeLinecap="round" strokeLinejoin="round" />,
  news: <g><rect x="2.5" y="3" width="11" height="10" rx="1" /><path d="M5 6 H11 M5 8.5 H11 M5 11 H8.5" /></g>,
  todo: <path d="M3 8 L6.5 11 L13 4" strokeLinecap="round" strokeLinejoin="round" />,
  notes: <g><path d="M3 2.5 h10 v8 l-3 3 H3 Z" strokeLinejoin="round" /><path d="M10 13.5 V10.5 H13" /></g>,
  clock: <g><circle cx="8" cy="8" r="6" /><path d="M8 4.5 V8 L10.5 9.5" /></g>,
  quote: <path d="M5 4 H3 v3 h2 c0 1.5-.7 2.3-2 2.6 M12 4 H10 v3 h2 c0 1.5-.7 2.3-2 2.6" strokeLinejoin="round" />,
  summary: <g><path d="M3 3 H13 M3 6 H13 M3 9 H10 M3 12 H8" strokeLinecap="round" /></g>,
  docs: <g><path d="M4 2.5 h5 L12 5.5 V13 H4 Z" strokeLinejoin="round" /><path d="M6 8 H10 M6 10.5 H9" /></g>,
  chain: <g><circle cx="4.5" cy="8" r="2" /><circle cx="11.5" cy="8" r="2" /><path d="M6.5 8 H9.5" /></g>,
  vision: <g><circle cx="8" cy="8" r="1.8" /><circle cx="3" cy="4.5" r="1.1" /><circle cx="13" cy="5" r="1.1" /><circle cx="4" cy="12.5" r="1.1" /><circle cx="12.5" cy="12" r="1.1" /><path d="M6.4 7 L4.2 5.4 M9.6 7 L11.8 5.7 M6.6 9.3 L4.8 11.3 M9.4 9.3 L11.4 11.1" /></g>,
  subagent: <g><circle cx="8" cy="5" r="2.2" /><path d="M3.5 13 a4.5 4.5 0 0 1 9 0" /></g>,
  mission: <g><circle cx="8" cy="8" r="5.5" /><circle cx="8" cy="8" r="1.6" /></g>,
  usage: <g><rect x="2.5" y="8" width="2.6" height="5" rx="0.6" /><rect x="6.7" y="5" width="2.6" height="8" rx="0.6" /><rect x="10.9" y="3" width="2.6" height="10" rx="0.6" /></g>,
  file: <g><path d="M4 2.5 h5 L12 5.5 V13 H4 Z" strokeLinejoin="round" /><path d="M9 2.5 V5.5 H12" /></g>,
  youtube: <g><rect x="2" y="3.5" width="12" height="9" rx="2" /><path d="M7 6.5 L10 8 L7 9.5 Z" fill="currentColor" stroke="none" /></g>,
  code: <g><path d="M6 4 L2.5 8 L6 12 M10 4 L13.5 8 L10 12" strokeLinecap="round" strokeLinejoin="round" /></g>,
  terminal: <g><rect x="2" y="3" width="12" height="10" rx="1.5" /><path d="M4.5 6.5 L6.5 8.5 L4.5 10.5 M8 10.5 H11" strokeLinecap="round" strokeLinejoin="round" /></g>,
  browser: <g><circle cx="8" cy="8" r="6" /><path d="M2 8 H14 M8 2 a9 9 0 0 1 0 12 a9 9 0 0 1 0-12" /></g>,
  blueprint: <g><rect x="2" y="3" width="12" height="10" rx="1" /><path d="M2 6 H14 M5 6 V13 M5 9 H9 M9 6 V11" strokeLinecap="round" /></g>,
  detect: <g><path d="M2 5 V3 a1 1 0 0 1 1-1 H5 M11 2 H13 a1 1 0 0 1 1 1 V5 M14 11 V13 a1 1 0 0 1-1 1 H11 M5 14 H3 a1 1 0 0 1-1-1 V11" strokeLinecap="round" /><rect x="6" y="6" width="4" height="4" rx="0.5" /></g>,
  spotify: <g><circle cx="8" cy="8" r="6" /><path d="M5 6.5 a8 8 0 0 1 6 0.6 M5.3 9 a6 6 0 0 1 5 0.5 M5.6 11 a4 4 0 0 1 4 0.4" strokeLinecap="round" /></g>,
};

/** The tile-type for an id ("image_abc" → image, "file_x" → file, "chat" → chat). */
export function tileTypeOf(id: string): string {
  if (!id) return "";
  if (id.startsWith("file_")) return "file";
  return multiInstanceKind(id) ?? id;
}

/** The raw glyph node for an id-or-type (used by the mobile Focus/Feed chrome). */
export function tileGlyphPath(idOrType: string): ReactNode {
  return GLYPHS[tileTypeOf(idOrType)] ?? null;
}

/** Icon for a tile head. Reads the tile id from context unless `type` is given.
 *  Renders nothing for types without a glyph (so heads never show a blank box). */
export default function TileGlyph({ type, size = 14 }: { type?: string; size?: number }) {
  const tileId = useTileId();
  const glyph = tileGlyphPath(type ?? tileId);
  if (!glyph) return null;
  return (
    <span className="tile-glyph" aria-hidden="true">
      <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.4">
        {glyph}
      </svg>
    </span>
  );
}
