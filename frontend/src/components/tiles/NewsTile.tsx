// News headlines tile. Backed by GET /news (newsapi.org).
//   - Empty search  -> general US top headlines, newest-published first.
//   - With a query   -> topic search (newsapi /everything, sorted newest-first).
// Backend caches per-query (~5min) and proxies the API key — never client-side.

import { useEffect, useRef, useState } from "react";
import TileGlyph from "../TileGlyph";
import { getNews, type Headline } from "../../api";
import { useBrowser } from "../../state/BrowserContext";
import { copyWithToast } from "../../native/actions";

function fmtRelative(iso: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

export default function NewsTile() {
  const { openInBrowser } = useBrowser();
  const [items, setItems] = useState<Headline[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [fetchedAt, setFetchedAt] = useState<string>("");

  // Raw input value + the debounced query that actually drives fetches.
  const [input, setInput] = useState<string>("");
  const [query, setQuery] = useState<string>("");

  // Debounce typing so we don't fire a request per keystroke.
  useEffect(() => {
    const id = setTimeout(() => setQuery(input.trim()), 400);
    return () => clearTimeout(id);
  }, [input]);

  // Keep the active query in a ref so the poll interval always fetches the
  // currently-shown feed (default or topic) without re-arming on every change.
  const queryRef = useRef(query);
  queryRef.current = query;

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await getNews(queryRef.current);
        if (!alive) return;
        setItems(r.headlines);
        setFetchedAt(r.fetched_at);
        setError(null);
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    setLoading(true);
    load();
    // Poll every 5 min. Backend per-query cache absorbs most of these.
    const id = setInterval(load, 5 * 60 * 1000);
    return () => { alive = false; clearInterval(id); };
  }, [query]);

  const emptyMsg = query ? "no results" : "no headlines";

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph /> NEWS</span>
        <span className="tile-title-meta">{items.length ? `${items.length} · ${fmtRelative(fetchedAt)}` : ""}</span>
      </div>
      <div
        className="tile-body tnews-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        <div className="tnews-search-wrap">
          <input
            className="tnews-input no-drag"
            type="text"
            value={input}
            placeholder="Search news"
            onChange={(e) => setInput(e.target.value)}
            onMouseDown={(e) => e.stopPropagation()}
            aria-label="Search news"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        {loading && <div className="tnews-loading">loading…</div>}
        {error && <div className="tnews-error">News unavailable</div>}
        {!loading && !error && items.length === 0 && <div className="tnews-loading">{emptyMsg}</div>}
        {!error && items.length > 0 && (
          <ul className="tnews-list">
            {items.map((h, i) => (
              <li className="tnews-item" key={i}>
                <button
                  type="button"
                  className="tnews-title no-drag"
                  /* A headline opens in the in-app reader tile, which fetches
                     and sanitizes server-side and can save the page to the
                     Vault with its URL kept as the citation. */
                  onClick={(e) => {
                    e.stopPropagation();
                    openInBrowser(h.url);
                  }}
                  /* Stop the mousedown from reaching the FreeCanvas drag
                     handler — but DON'T preventDefault, so the click still
                     fires normally. */
                  onMouseDown={(e) => e.stopPropagation()}
                  title={h.url}
                >
                  {h.title}
                </button>
                <div className="tnews-meta">
                  <span className="tnews-src">{h.source}</span>
                  <span>{fmtRelative(h.published_at)}</span>
                  <button
                    type="button"
                    className="tnews-copy no-drag"
                    aria-label="Copy link"
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={(e) => { e.stopPropagation(); void copyWithToast(h.url, "Link copied"); }}
                  >⧉</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
