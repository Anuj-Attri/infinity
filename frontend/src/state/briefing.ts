// Daily greeting (warm friend's hello — time-of-day aware).
//
// The first time the user sends a chat message on a new LOCAL calendar day,
// Nova opens her reply with a warm, witty greeting matched to the user's actual
// LOCAL time of day (morning / afternoon / evening — never a hardcoded "good
// morning"), weaving real tile data (Weather, Portfolio, News, Tasks) into
// conversational PROSE — a sharp friend sharing things over coffee, NOT a
// briefing/status report — then answers whatever they actually typed. (The daily
// niche game/anime quote tile was retired.)
//
// "First message of a new day" is tracked by persisting the last-briefed local
// date in localStorage, so it survives app restarts and only fires once/day.
// Both the day-rollover AND the greeting derive from the DEVICE's local clock
// (new Date() — the user's machine TZ, its own zone), never UTC.
// The data is gathered through the existing tile-read path (gatherTileData).

import { gatherTileData } from "./tileRegistry";

const BRIEF_KEY = "infinity.briefing.lastDate.v1";

function todayLocalISO(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Time-of-day greeting from the DEVICE's LOCAL clock (the device's own zone). */
function localGreeting(): { salutation: string; period: string } {
  const h = new Date().getHours();           // local hours, 0–23
  if (h < 5)  return { salutation: "Hey",            period: "late night" };
  if (h < 12) return { salutation: "Good morning",   period: "morning" };
  if (h < 17) return { salutation: "Good afternoon", period: "afternoon" };
  if (h < 22) return { salutation: "Good evening",   period: "evening" };
  return { salutation: "Good evening", period: "night" };
}

/** True on the first call of a new local calendar day (and on very first use). */
export function shouldBriefToday(): boolean {
  try { return localStorage.getItem(BRIEF_KEY) !== todayLocalISO(); }
  catch { return false; }
}

/** Stamp today as briefed so we never double-brief in the same day. */
export function markBriefedToday(): void {
  try { localStorage.setItem(BRIEF_KEY, todayLocalISO()); } catch { /* ignore */ }
}

/**
 * Build the briefing instruction + live tile data to prepend to the user's
 * first message of the day. The local model writes the actual prose; this only
 * supplies the framing and the gathered data. Never throws.
 */
export async function buildBriefingContext(): Promise<string> {
  let data = "";
  try {
    const blocks = await gatherTileData(["weather", "portfolio", "news", "tasks"]);
    data = blocks.join("\n\n");
  } catch {
    data = "";  // fail-closed: still greets warmly, just without the details
  }

  const { salutation, period } = localGreeting();

  const instruction = [
    `[FIRST HELLO OF THE DAY — it is currently ${period} for them (local time).`,
    `Open your reply with a warm "${salutation}" greeting that fits the ${period},`,
    "THEN answer their actual message below the ---.]",
    "",
    "Greet them like a sharp friend who happens to know things and shares them",
    "easily — NOT a briefing, NOT a status report, NOT research. Small talk, in",
    "flowing PROSE. A couple of sentences, warm and present.",
    "",
    `Match the time of day: it's ${period}, so greet accordingly — do NOT say`,
    "\"good morning\" unless it's actually morning. If it's late, a light nod to the",
    "hour is welcome (never make them feel judged for the time).",
    "",
    "Weave in a few real details from the live data below — the weather, how their",
    "holdings are moving, a notable headline, what's still on their plate — but",
    "naturally, the way a friend mentions things over coffee. Be a little witty:",
    "connect two things, land a light pun if it's there (e.g. heavy rain AND a",
    "stock sliding -> \"rain isn't the only thing pouring down today\"). Never make",
    "them feel behind.",
    "",
    "Greet them by name if you know it. Use ONLY details that have real data —",
    "silently skip anything empty or unavailable; never invent or pad. NO lists,",
    "NO bullets, NO headers — it's a greeting, not a dashboard. End with an open,",
    "easy invitation (e.g. \"what do you want to get into?\").",
    "",
    // The example carried a hardcoded first name, which the model copied
    // straight into the greeting — so every account would have been welcomed
    // by the same person's name. Whose name to use (if any) comes from the
    // account, via the identity line the backend appends to the persona.
    `Tone reference (match this feel, don't copy the words): "${salutation} — looks`,
    "like heavy rain today, and it's not the only thing pouring down with Nvidia",
    "sliding too. What would you like to do?\"",
  ].join("\n");

  return `${instruction}\n\nLIVE DATA (weave naturally, skip anything empty):\n${data}`;
}
