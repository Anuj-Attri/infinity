// Slash-command registry. Each tile registers a "provide" function that
// returns its CURRENT live data as a text block. When a chat message
// contains `/tile` tokens, those blocks are prepended to the prompt sent
// to Nova for that turn. No memory, no persistence — explicit per-turn.

import { getNews, getStocks, getWeather } from "../api";

export type TileSpec = {
  id: string;          // slash key, e.g. "portfolio"
  label: string;       // shown in autocomplete (e.g. "/portfolio")
  description: string; // tagline under label
  provide: () => Promise<string>;
};

const STOCKS_KEY  = "infinity.stocks.v1";
const NOTES_KEY   = "supernova.notes.v1";
const TODO_KEY    = "supernova.todo.v1";
const CLOCKS_KEY  = "infinity.clocks.v1";
const WEATHER_KEY = "infinity.weather.city.v1";

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch { return fallback; }
}

function fmtPct(p: number | null | undefined): string {
  if (p === null || p === undefined) return "n/a";
  return `${p > 0 ? "+" : ""}${p.toFixed(2)}%`;
}

export const TILES: TileSpec[] = [
  {
    id: "portfolio",
    label: "/portfolio",
    description: "Live watchlist with current quotes",
    provide: async () => {
      const syms = readJson<string[]>(STOCKS_KEY, []);
      if (!syms.length) return "[/portfolio] (empty watchlist)";
      try {
        const { quotes } = await getStocks(syms);
        const rows = quotes.map((q) => {
          if (q.error) return `  ${q.symbol}: (error: ${q.error})`;
          return `  ${q.symbol}: $${(q.price ?? 0).toFixed(2)}  ${fmtPct(q.change_pct)}`;
        });
        return `[/portfolio — current quotes]\n${rows.join("\n")}`;
      } catch (e) {
        return `[/portfolio unavailable: ${e instanceof Error ? e.message : String(e)}]`;
      }
    },
  },
  {
    id: "news",
    label: "/news",
    description: "Current top headlines",
    provide: async () => {
      try {
        const { headlines } = await getNews();
        if (!headlines.length) return "[/news] (no headlines)";
        const rows = headlines.slice(0, 10).map((h, i) =>
          `  ${i + 1}. ${h.title} — ${h.source}`
        );
        return `[/news — current top headlines]\n${rows.join("\n")}`;
      } catch (e) {
        return `[/news unavailable: ${e instanceof Error ? e.message : String(e)}]`;
      }
    },
  },
  {
    id: "clock",
    label: "/clock",
    description: "Saved world-clock cities + current time",
    provide: async () => {
      const saved = readJson<Array<{ city?: string; label?: string; country?: string; tz: string }>>(CLOCKS_KEY, []);
      if (!saved.length) {
        const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
        const now = new Date().toLocaleString([], { timeZone: localTz });
        return `[/clock — local only]\n  Local (${localTz}): ${now}`;
      }
      const now = new Date();
      const rows = saved.map((c) => {
        const tz = c.tz;
        const name = c.city || c.label || tz;
        const country = c.country ? `, ${c.country}` : "";
        let timeStr = "";
        try {
          timeStr = new Intl.DateTimeFormat([], {
            weekday: "short", hour: "2-digit", minute: "2-digit",
            timeZone: tz,
          }).format(now);
        } catch { timeStr = "?"; }
        return `  ${name}${country} (${tz}): ${timeStr}`;
      });
      return `[/clock — current times]\n${rows.join("\n")}`;
    },
  },
  {
    id: "weather",
    label: "/weather",
    description: "Today's forecast for your saved city",
    provide: async () => {
      const city = (localStorage.getItem(WEATHER_KEY) || "New York").trim() || "New York";
      try {
        const w = await getWeather(city);
        const place = w.city + (w.country ? `, ${w.country}` : "");
        const lines = [`[/weather — ${place}]`];
        const c = w.current;
        if (c) {
          const feels = c.feels_like_c ?? c.temp_c;
          lines.push(`  now: ${c.condition}, ${Math.round(c.temp_c ?? 0)}°C${feels != null ? ` (feels ${Math.round(feels)}°)` : ""}`);
        }
        const today = w.daily?.[0];
        if (today) {
          const precip = today.precip_pct != null && today.precip_pct > 0 ? `, ${today.precip_pct}% precip` : "";
          lines.push(`  today: ${today.condition}, high ${Math.round(today.high_c ?? 0)}° / low ${Math.round(today.low_c ?? 0)}°${precip}`);
        }
        return lines.join("\n");
      } catch (e) {
        return `[/weather unavailable: ${e instanceof Error ? e.message : String(e)}]`;
      }
    },
  },
  {
    id: "tasks",
    label: "/tasks",
    description: "Current to-do list (open + done)",
    provide: async () => {
      const tasks = readJson<Array<{ text: string; done: boolean }>>(TODO_KEY, []);
      if (!tasks.length) return "[/tasks] (empty)";
      const open = tasks.filter((t) => !t.done);
      const done = tasks.filter((t) => t.done);
      const lines = [
        `[/tasks — ${open.length} open, ${done.length} done]`,
        ...open.map((t) => `  ☐ ${t.text}`),
        ...done.map((t) => `  ☑ ${t.text}`),
      ];
      return lines.join("\n");
    },
  },
  {
    id: "notes",
    label: "/notes",
    description: "Scratchpad text",
    provide: async () => {
      const text = (localStorage.getItem(NOTES_KEY) || "").trim();
      if (!text) return "[/notes] (empty)";
      return `[/notes]\n${text}`;
    },
  },
];

const TILE_BY_ID = new Map(TILES.map((t) => [t.id, t]));

// Dynamic tiles registered at runtime (e.g. attached files). Same shape
// as TILES so they ride the same /command silent-injection path. Keyed by
// `id` (which is also the slash-token). Mutations notify listeners so the
// bubble's autocomplete can refresh without polling.
const EXTRA_TILES = new Map<string, TileSpec>();
const LISTENERS = new Set<() => void>();

export function registerExtraTile(spec: TileSpec): void {
  EXTRA_TILES.set(spec.id, spec);
  LISTENERS.forEach((cb) => { try { cb(); } catch { /* ignore */ } });
}
export function unregisterExtraTile(id: string): void {
  if (!EXTRA_TILES.delete(id)) return;
  LISTENERS.forEach((cb) => { try { cb(); } catch { /* ignore */ } });
}
export function subscribeExtraTiles(cb: () => void): () => void {
  LISTENERS.add(cb);
  return () => { LISTENERS.delete(cb); };
}
export function getAllTiles(): TileSpec[] {
  return [...TILES, ...EXTRA_TILES.values()];
}
function resolveTile(id: string): TileSpec | undefined {
  return TILE_BY_ID.get(id) ?? EXTRA_TILES.get(id);
}

// Strip trailing punctuation that's often typed right after a slash token.
const TOKEN_RE = /\/([a-zA-Z][\w-]*)/g;

export function extractSlashIds(message: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  let m: RegExpExecArray | null;
  while ((m = TOKEN_RE.exec(message)) !== null) {
    const id = m[1].toLowerCase();
    if (resolveTile(id) && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

// Gather specific tiles' live data by id — same read path + per-block
// fail-closed handling as the slash injection. Used by the Daily Briefing so
// it never builds its own tile readers. Unknown ids yield an "unavailable"
// marker rather than throwing.
export async function gatherTileData(ids: string[]): Promise<string[]> {
  return Promise.all(
    ids.map(async (id) => {
      const tile = resolveTile(id);
      if (!tile) return `[/${id} unavailable]`;
      try {
        return await tile.provide();
      } catch (e) {
        return `[/${id} unavailable: ${e instanceof Error ? e.message : String(e)}]`;
      }
    }),
  );
}

export async function buildContextForMessage(message: string): Promise<{
  prompt: string;
  usedTiles: string[];
}> {
  const ids = extractSlashIds(message);
  if (ids.length === 0) return { prompt: message, usedTiles: [] };
  const blocks = await Promise.all(
    ids.map(async (id) => {
      const tile = resolveTile(id)!;
      try {
        return await tile.provide();
      } catch (e) {
        return `[/${id} unavailable: ${e instanceof Error ? e.message : String(e)}]`;
      }
    }),
  );
  const header =
    "The user referenced tile(s) below with /slash commands. " +
    "Use this live data verbatim — do not hallucinate other tiles' contents.";
  const prompt =
    `${header}\n\n${blocks.join("\n\n")}\n\n---\n\n${message}`;
  return { prompt, usedTiles: ids };
}
