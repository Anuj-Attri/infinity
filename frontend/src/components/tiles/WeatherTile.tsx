// Weather tile — Open-Meteo, no auth needed.
// Nova can spawn this tile and set the city via edit_tile.

import { useCallback, useEffect, useRef, useState } from "react";
import { getWeather, type WeatherData } from "../../api";
import { onTileWrite } from "../../state/tileActions";
import TileGlyph from "../TileGlyph";

function wmoEmoji(code: number | null): string {
  if (code === null) return "";
  if (code === 0)    return "☀️";
  if (code <= 2)     return "🌤️";
  if (code <= 3)     return "☁️";
  if (code <= 48)    return "🌫️";
  if (code <= 57)    return "🌦️";
  if (code <= 67)    return "🌧️";
  if (code <= 77)    return "❄️";
  if (code <= 82)    return "🌧️";
  if (code <= 86)    return "🌨️";
  if (code <= 99)    return "⛈️";
  return "🌡️";
}

function conditionEmoji(cond: string): string {
  const c = cond.toLowerCase();
  if (c.includes("thunder"))                         return "⛈️";
  if (c.includes("snow") || c.includes("blizzard"))  return "❄️";
  if (c.includes("sleet") || c.includes("drizzle") || c.includes("rain")) return "🌧️";
  if (c.includes("fog") || c.includes("mist") || c.includes("haze")) return "🌫️";
  if (c.includes("overcast"))                        return "☁️";
  if (c.includes("cloud"))                           return "🌤️";
  if (c.includes("clear") || c.includes("sunny"))    return "☀️";
  return "🌡️";
}

function formatTemp(c: number | null): string {
  if (c === null) return "—";
  return `${Math.round(c)}°`;
}

function formatDayLabel(dateStr: string, idx: number): string {
  if (idx === 0) return "Today";
  const d = new Date(dateStr + "T12:00:00Z");
  return d.toLocaleDateString("en", { weekday: "short", timeZone: "UTC" });
}

// Persist the chosen city so the Daily Briefing's /weather reader knows which
// city to fetch (the tile is the source of truth for "your" weather).
const WEATHER_CITY_KEY = "infinity.weather.city.v1";
function loadCity(): string {
  try { return (localStorage.getItem(WEATHER_CITY_KEY) || "").trim() || "New York"; }
  catch { return "New York"; }
}

// Last-good forecast cache (per city, capped). When every live source fails,
// the tile renders this with a "stale · updated Xh ago" chip instead of going
// blank — blank only if a city has NEVER had a good fetch.
const WEATHER_CACHE_KEY = "infinity.weather.lastgood.v1";
type CacheEntry = { data: WeatherData; at: number };
function readCache(city: string): CacheEntry | null {
  try {
    const all = JSON.parse(localStorage.getItem(WEATHER_CACHE_KEY) || "{}");
    return all[city.trim().toLowerCase()] ?? null;
  } catch { return null; }
}
function writeCache(city: string, data: WeatherData): void {
  try {
    const all = JSON.parse(localStorage.getItem(WEATHER_CACHE_KEY) || "{}");
    all[city.trim().toLowerCase()] = { data, at: Date.now() };
    const keys = Object.keys(all);
    if (keys.length > 8) {                       // cap: drop the oldest cities
      keys.sort((a, b) => (all[a].at ?? 0) - (all[b].at ?? 0));
      for (const k of keys.slice(0, keys.length - 8)) delete all[k];
    }
    localStorage.setItem(WEATHER_CACHE_KEY, JSON.stringify(all));
  } catch { /* cache is best-effort */ }
}
function staleLabel(at: number): string {
  const mins = Math.max(1, Math.round((Date.now() - at) / 60_000));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export default function WeatherTile() {
  const [inputVal, setInputVal]   = useState(loadCity);
  const [activeCity, setActiveCity] = useState(loadCity);
  const [data, setData]           = useState<WeatherData | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [staleAt, setStaleAt] = useState<number | null>(null);

  const fetchWeather = useCallback(async (city: string) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    const signal = abortRef.current.signal;
    setLoading(true);
    setError(null);
    const c = city.trim();

    // ONE source: our backend, then the last-good cache.
    //
    // Mark 3 called Open-Meteo and wttr.in straight from the browser and only
    // fell back to the backend. That was resilience for a local-first app whose
    // backend might be switched off. Hosted, it is backwards: it hands the
    // user's IP and city to two third parties from their own browser, the calls
    // cannot be cached or rate-limited by us, and any Content-Security-Policy
    // worth having blocks them. If our backend is down, nothing works anyway.
    //
    // Upstream redundancy belongs server-side, where it is one request from one
    // address, not one per user. "City not found" is a definitive answer, not an
    // outage — it stops the chain immediately.
    const sources: Array<() => Promise<WeatherData>> = [
      () => getWeather(c, signal),
    ];
    for (const src of sources) {
      try {
        const d = await src();
        setData(d);
        setStaleAt(null);
        writeCache(c, d);
        setLoading(false);
        return;
      } catch (e: unknown) {
        if (e instanceof Error && e.name === "AbortError") return;
        if (e instanceof Error && /city not found/i.test(e.message)) {
          setError(e.message);
          setLoading(false);
          return;
        }
        // outage-shaped failure → next source
      }
    }

    // Every live source failed → render the last-good forecast, marked stale.
    const cached = readCache(c);
    if (cached) {
      setData(cached.data);
      setStaleAt(cached.at);
    } else {
      setError("Weather sources unreachable.");
    }
    setLoading(false);
  }, []);

  useEffect(() => { void fetchWeather(loadCity()); }, [fetchWeather]);

  // Nova set the city (e.g. spawned weather for the user's known location) →
  // reflect it live: reload the saved city and refetch.
  useEffect(() => onTileWrite("weather", () => {
    const c = loadCity();
    setInputVal(c);
    setActiveCity(c);
    void fetchWeather(c);
  }), [fetchWeather]);

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = inputVal.trim();
    if (!trimmed) return;
    setActiveCity(trimmed);
    try { localStorage.setItem(WEATHER_CITY_KEY, trimmed); } catch { /* ignore */ }
    void fetchWeather(trimmed);
  };

  const forecast = data?.daily.slice(0, 5) ?? [];
  const cityMeta = data ? data.city + (data.country ? `, ${data.country}` : "") : activeCity;

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Weather</span>
        <span className="tile-title-meta">{loading ? "loading…" : cityMeta}</span>
      </div>
      <div
        className="tile-body weather-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {/* Search row */}
        <form className="weather-search-row" onSubmit={onSearch}>
          <input
            className="weather-input"
            value={inputVal}
            onChange={(e) => setInputVal(e.target.value)}
            placeholder="City…"
            aria-label="City"
          />
          <button type="submit" className="weather-icon-btn" title="Search" aria-label="Search">
            <svg viewBox="0 0 14 14" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
              <circle cx="6" cy="6" r="4.5" />
              <path d="M9.5 9.5 L12.5 12.5" strokeLinecap="round" />
            </svg>
          </button>
          <button
            type="button"
            className="weather-icon-btn"
            title="Refresh"
            aria-label="Refresh"
            onClick={() => void fetchWeather(activeCity)}
          >
            <svg viewBox="0 0 14 14" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M12 7 A5 5 0 1 1 7 2" strokeLinecap="round" />
              <path d="M7 2 L9.5 2 L9.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </form>

        {loading && <div className="weather-status">Loading…</div>}
        {error   && <div className="weather-status weather-error-msg">{error}</div>}
        {staleAt !== null && !loading && (
          <div className="weather-stale" title="every live weather source failed; showing the last good forecast">
            stale · updated {staleLabel(staleAt)}
          </div>
        )}

        {data && !loading && (
          <>
            {/* Current conditions */}
            <div className="weather-hero">
              {data.region && (
                <div className="weather-region">{data.region}{data.country ? `, ${data.country}` : ""}</div>
              )}
              <div className="weather-main-row">
                {/* Some sources carry no WMO code → derive the emoji from the condition text */}
                <span className="weather-big-emoji">
                  {data.current.weather_code !== null
                    ? wmoEmoji(data.current.weather_code)
                    : conditionEmoji(data.current.condition)}
                </span>
                <span className="weather-big-temp">{formatTemp(data.current.temp_c)}</span>
              </div>
              <div className="weather-condition-label">{data.current.condition}</div>
              <div className="weather-meta-row">
                {data.current.feels_like_c !== null && (
                  <span>Feels {formatTemp(data.current.feels_like_c)}</span>
                )}
                {data.current.humidity_pct !== null && (
                  <span>{data.current.humidity_pct}%</span>
                )}
                {data.current.wind_kmh !== null && (
                  <span>Wind {Math.round(data.current.wind_kmh)} km/h</span>
                )}
              </div>
            </div>

            {/* 5-day forecast */}
            {forecast.length > 0 && (
              <div className="weather-forecast">
                {forecast.map((day, i) => (
                  <div key={i} className="weather-day">
                    <div className="weather-day-name">{formatDayLabel(day.date, i)}</div>
                    <div className="weather-day-icon">{conditionEmoji(day.condition)}</div>
                    <div className="weather-day-cond">{day.condition.split(" ").slice(0, 2).join(" ")}</div>
                    <div className="weather-day-temps">
                      <span className="weather-day-hi">{formatTemp(day.high_c)}</span>
                      <span className="weather-day-lo">{formatTemp(day.low_c)}</span>
                    </div>
                    {day.precip_pct !== null && day.precip_pct > 20 && (
                      <div className="weather-day-precip">💧{day.precip_pct}%</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
