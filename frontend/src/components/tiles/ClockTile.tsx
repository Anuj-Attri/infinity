// World Clock — fast city lookup.
// One search input → shows "City, Country — HH:MM" matches for any city
// in the bundled table. Selecting a match adds it to the list (persisted
// to localStorage). Dropdown overlays the tile, doesn't push layout.

import { useEffect, useMemo, useRef, useState } from "react";
import { useSettings } from "../../state/SettingsContext";
import TileGlyph from "../TileGlyph";
import { CITIES, searchCities, type CityEntry } from "../../data/cities";

const KEY = "infinity.clocks.v1";

type City = { city: string; country: string; tz: string };

function localCity(): City {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  // Try to match the device tz to our table for a nice city/country pair.
  const match = CITIES.find((c) => c.tz === tz);
  if (match) return { city: match.city, country: match.country, tz: match.tz };
  const last = tz.split("/").pop() ?? tz;
  return { city: last.replace(/_/g, " "), country: "Local", tz };
}

function loadCities(): City[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [localCity()];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [localCity()];
    const clean = parsed
      .filter((c) => c && typeof c.tz === "string")
      .map((c) => ({
        city: typeof c.city === "string" ? c.city : (typeof c.label === "string" ? c.label : c.tz),
        country: typeof c.country === "string" ? c.country : "",
        tz: c.tz,
      }));
    return clean.length ? clean : [localCity()];
  } catch { return [localCity()]; }
}

function saveCities(cs: City[]) {
  try { localStorage.setItem(KEY, JSON.stringify(cs)); } catch { /* ignore */ }
}

function fmtTime(tz: string, now: Date, h12: boolean): string {
  try {
    return new Intl.DateTimeFormat([], {
      hour: "2-digit", minute: "2-digit", hour12: h12, timeZone: tz,
    }).format(now);
  } catch {
    return "--:--";
  }
}
function fmtDate(tz: string, now: Date): string {
  try {
    return new Intl.DateTimeFormat([], {
      weekday: "short", month: "short", day: "numeric", timeZone: tz,
    }).format(now);
  } catch { return ""; }
}

export default function ClockTile() {
  const { settings } = useSettings();
  const h12 = settings.hourFormat === "12";

  const [cities, setCities] = useState<City[]>(() => loadCities());
  const [now, setNow] = useState(() => new Date());
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => { saveCities(cities); }, [cities]);

  // Search across the bundled table whenever the query changes.
  const results: CityEntry[] = useMemo(() => searchCities(query, 12), [query]);

  const addCity = (e: CityEntry) => {
    // Allow duplicates only if tz is different; same tz + same city = no-op.
    const exists = cities.some((c) => c.tz === e.tz && c.city === e.city);
    if (!exists) {
      setCities((prev) => [...prev, { city: e.city, country: e.country, tz: e.tz }]);
    }
    setQuery("");
    setOpen(false);
    // Refocus the input for fast successive lookups.
    requestAnimationFrame(() => searchRef.current?.focus());
  };

  const remove = (i: number) => {
    setCities((prev) => prev.filter((_, idx) => idx !== i));
  };

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />World Clock</span>
        <span className="tile-title-meta">{cities.length} {cities.length === 1 ? "city" : "cities"}</span>
      </div>

      <div
        className="tile-body tclock-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {/* Search — always visible at the top of the tile body. */}
        <div className="tclock-search-wrap no-drag" onMouseDown={(e) => e.stopPropagation()}>
          <input
            ref={searchRef}
            className="tclock-search no-drag"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onBlur={() => window.setTimeout(() => setOpen(false), 150)}
            onKeyDown={(e) => {
              if (e.key === "Escape") { setOpen(false); setQuery(""); }
              else if (e.key === "Enter" && results.length > 0) { e.preventDefault(); addCity(results[0]); }
            }}
            placeholder="Search any city or country…"
            spellCheck={false}
          />
          {open && query.trim() && (
            <div className="tclock-results">
              {results.length === 0 && (
                <div className="tclock-results-empty">No matches.</div>
              )}
              {results.map((r, i) => (
                <button
                  type="button"
                  key={`${r.tz}-${r.city}-${i}`}
                  className="tclock-result no-drag"
                  // mousedown fires before blur → use mousedown so the
                  // input doesn't blur-and-cancel before the click lands.
                  onMouseDown={(e) => { e.preventDefault(); addCity(r); }}
                >
                  <span className="tclock-result-place">
                    {r.city}, <span className="tclock-result-country">{r.country}</span>
                  </span>
                  <span className="tclock-result-time">{fmtTime(r.tz, now, h12)}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Saved cities */}
        <div className="tclock-list">
          {cities.length === 0 && <div className="tclock-empty">No cities saved yet — search above.</div>}
          {cities.map((c, i) => (
            <div className={`tclock-row ${i === 0 ? "primary" : ""}`} key={`${c.tz}-${i}`}>
              <div className="tclock-row-text">
                <div className="tclock-city">
                  {c.city}{c.country ? `, ${c.country}` : ""}
                </div>
                <div className="tclock-sub">{fmtDate(c.tz, now)}</div>
              </div>
              <div className="tclock-time">{fmtTime(c.tz, now, h12)}</div>
              {cities.length > 1 && (
                <button
                  type="button"
                  className="tclock-rm no-drag"
                  onClick={() => remove(i)}
                  onMouseDown={(e) => e.stopPropagation()}
                  title="Remove"
                  aria-label={`Remove ${c.city}`}
                >
                  <svg viewBox="0 0 10 10" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1.2">
                    <path d="M2 2 L8 8 M8 2 L2 8" />
                  </svg>
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
