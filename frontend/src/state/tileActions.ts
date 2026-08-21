// Cross-tile action helpers for Nova's tools.
//
//  - Permission boundary: Nova may read/write/spawn dashboard tiles, but NEVER
//    Terminal / Browser / Code (user-only surfaces). isForbiddenTile() is the
//    single guard; every tool path checks it.
//  - Write bus: writes update the tile's localStorage AND emit a CustomEvent so
//    the mounted tile reloads live (NotesTile / TodoTile subscribe via onTileWrite).
//  - Market/news search reuses the EXISTING data endpoints (/news, /stocks,
//    /stocks/movers) — no agent dispatch, no cloud spend.

import { getNews, getStocks, getStockMovers } from "../api";

const NOTES_KEY   = "supernova.notes.v1";
const TODO_KEY    = "supernova.todo.v1";
const WEATHER_KEY = "infinity.weather.city.v1";

// User-only surfaces Nova must never read or act on. Multi-instance ids look
// like "terminal_ab12", "code_x9", "browser_q3" — match the kind prefix too.
const FORBIDDEN_KINDS = new Set(["terminal", "browser", "code"]);

export function isForbiddenTile(id: string): boolean {
  const kind = id.includes("_") ? id.slice(0, id.indexOf("_")) : id;
  return FORBIDDEN_KINDS.has(kind.toLowerCase());
}

// ---- write bus --------------------------------------------------------------

export type TileWriteTarget = "notes" | "tasks" | "weather";
const WRITE_EVENT = "infinity:tilewrite";

function emitWrite(target: TileWriteTarget) {
  try { window.dispatchEvent(new CustomEvent(WRITE_EVENT, { detail: { target } })); } catch { /* ignore */ }
}

/** Subscribe a mounted tile to external writes for its target. Returns an unsubscribe. */
export function onTileWrite(target: TileWriteTarget, cb: () => void): () => void {
  const handler = (e: Event) => {
    const d = (e as CustomEvent).detail;
    if (d && d.target === target) cb();
  };
  window.addEventListener(WRITE_EVENT, handler);
  return () => window.removeEventListener(WRITE_EVENT, handler);
}

/** Append a block of text to the Notes scratchpad. Returns a short confirmation. */
export function appendNote(text: string): string {
  const add = (text || "").trim();
  if (!add) throw new Error("empty note");
  let cur = "";
  try { cur = localStorage.getItem(NOTES_KEY) ?? ""; } catch { cur = ""; }
  const next = cur.trim() ? `${cur.trimEnd()}\n\n${add}` : add;
  localStorage.setItem(NOTES_KEY, next);
  emitWrite("notes");
  return "Added to Notes.";
}

/** Set the Weather tile's city (so a weather spawn shows the user's known
 *  location). Persists + notifies a mounted WeatherTile to refetch. */
export function setWeatherCity(city: string): void {
  const c = (city || "").trim();
  if (!c) return;
  try { localStorage.setItem(WEATHER_KEY, c); } catch { /* ignore */ }
  emitWrite("weather");
}

type Task = { id: string; text: string; done: boolean; ts: number };

/** Add one or more tasks (newline-separated content => multiple). Returns confirmation. */
export function addTasks(content: string): string {
  const lines = (content || "")
    .split("\n")
    .map((l) => l.replace(/^[-*•\d.)\s]+/, "").trim())
    .filter(Boolean);
  if (lines.length === 0) throw new Error("no tasks");
  let cur: Task[] = [];
  try {
    const raw = localStorage.getItem(TODO_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) cur = parsed.filter((t) => t && typeof t.text === "string");
  } catch { cur = []; }
  const added: Task[] = lines.map((text) => ({
    id: (crypto as Crypto).randomUUID ? crypto.randomUUID() : `${Date.now()}_${Math.random()}`,
    text, done: false, ts: Date.now(),
  }));
  localStorage.setItem(TODO_KEY, JSON.stringify([...cur, ...added]));
  emitWrite("tasks");
  return added.length === 1 ? "Added 1 task." : `Added ${added.length} tasks.`;
}

// ---- market / news search ---------------------------------------------------

/**
 * Run a read-only market/news search via existing data endpoints and return a
 * compact text block for Nova to summarize. Never advises; just surfaces data.
 * Fails closed with a clean string (no thrown stack, no leaked URL).
 */
export async function marketSearch(mode: string, query: string): Promise<string> {
  const q = (query || "").trim();
  try {
    if (mode === "movers") {
      const r = await getStockMovers();
      const fmt = (m: { symbol: string; change_pct: number | null; price: number | null }) =>
        `  ${m.symbol}: ${m.price ?? "?"} (${m.change_pct == null ? "n/a" : (m.change_pct > 0 ? "+" : "") + m.change_pct.toFixed(2) + "%"})`;
      const g = r.gainers.slice(0, 6).map(fmt).join("\n");
      const l = r.losers.slice(0, 6).map(fmt).join("\n");
      return `[market_search: today's movers]\nGainers:\n${g || "  (none)"}\nLosers:\n${l || "  (none)"}`;
    }
    if (mode === "ticker" && q) {
      const sym = q.toUpperCase();
      const [quotes, news] = await Promise.all([
        getStocks([sym]).then((r) => r.quotes).catch(() => []),
        getNews(sym).then((r) => r.headlines).catch(() => []),
      ]);
      const qline = quotes.map((x) =>
        x.error ? `  ${x.symbol}: (unavailable)` :
        `  ${x.symbol}: ${x.price ?? "?"} (${x.change_pct == null ? "n/a" : (x.change_pct > 0 ? "+" : "") + x.change_pct.toFixed(2) + "%"})`
      ).join("\n") || `  ${sym}: (no quote)`;
      const heads = news.slice(0, 6).map((h, i) => `  ${i + 1}. ${h.title} — ${h.source}`).join("\n") || "  (no recent headlines)";
      return `[market_search: ${sym}]\nQuote:\n${qline}\nHeadlines:\n${heads}`;
    }
    // default: news search (topic) or general headlines
    const { headlines } = await getNews(q);
    if (!headlines.length) return `[market_search: news${q ? ` "${q}"` : ""}] (no headlines)`;
    const rows = headlines.slice(0, 8).map((h, i) => `  ${i + 1}. ${h.title} — ${h.source}`).join("\n");
    return `[market_search: news${q ? ` "${q}"` : ""}]\n${rows}`;
  } catch {
    return "[market_search unavailable]";
  }
}
