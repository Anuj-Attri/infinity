// Markets / My Portfolio tile.
//
// Watchlist rows render from `symbols` (not from `quotes`), so a freshly-
// added ticker shows immediately with a "…" placeholder until its quote
// arrives. Adding a ticker also selects it so the graph swaps over right
// away.
//
// Graph: SVG line chart with labeled X (time) + Y (price) axes, header
// showing the active symbol, price, and %change. Range dropdown maps to
// yfinance period/interval pairs server-side.

import { useEffect, useMemo, useRef, useState } from "react";
import TileGlyph from "../TileGlyph";
import {
  getStocks, getStockHistory, getStockSummary, searchStocks, getStockMovers,
  type Quote, type HistoryPoint, type StockRange, type SearchResult, type Mover,
} from "../../api";

type PortfolioView = "holdings" | "movers";

const SYMS_KEY = "infinity.stocks.v1";
const RANGE_KEY = "infinity.stocks.range.v1";
const DEFAULTS = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"];

const RANGES: { key: StockRange; label: string }[] = [
  { key: "1m",  label: "1 minute" },
  { key: "5m",  label: "5 minutes" },
  { key: "15m", label: "15 minutes" },
  { key: "1h",  label: "1 hour" },
  { key: "1D",  label: "1 day" },
  { key: "1W",  label: "1 week" },
  { key: "1M",  label: "1 month" },
  { key: "3M",  label: "3 months" },
  { key: "1Y",  label: "1 year" },
];

function loadSymbols(): string[] {
  try {
    const raw = localStorage.getItem(SYMS_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULTS;
    const clean = parsed
      .filter((s) => typeof s === "string")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    return clean.length ? clean : DEFAULTS;
  } catch { return DEFAULTS; }
}
function saveSymbols(syms: string[]) {
  try { localStorage.setItem(SYMS_KEY, JSON.stringify(syms)); } catch { /* ignore */ }
}

function loadRange(): StockRange {
  try {
    const raw = localStorage.getItem(RANGE_KEY);
    if (raw && RANGES.some((r) => r.key === raw)) return raw as StockRange;
  } catch { /* ignore */ }
  return "1D";
}
function saveRange(r: StockRange) {
  try { localStorage.setItem(RANGE_KEY, r); } catch { /* ignore */ }
}

// Format a timestamp tick label compactly based on the range.
function fmtTickFor(rangeKey: StockRange, iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  switch (rangeKey) {
    case "1m":
    case "5m":
    case "15m":
    case "1h":
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    case "1D":
    case "1W":
      return d.toLocaleDateString([], { month: "short", day: "numeric" });
    case "1M":
    case "3M":
    case "1Y":
    default:
      return d.toLocaleDateString([], { month: "short", year: "2-digit" });
  }
}

function Chart({
  points, up, rangeKey,
}: {
  points: HistoryPoint[];
  up: boolean;
  rangeKey: StockRange;
}) {
  if (points.length < 2) return <div className="tstock-spark-empty">no data</div>;

  // SVG dimensions in viewBox units.
  const W = 320;
  const H = 110;
  const PAD_L = 36;
  const PAD_R = 4;
  const PAD_T = 6;
  const PAD_B = 18;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;

  const prices = points.map((p) => p.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = Math.max(1e-6, max - min);

  const stepX = plotW / (points.length - 1);
  const coords = points.map((p, i) => {
    const x = PAD_L + i * stepX;
    const y = PAD_T + (1 - (p.price - min) / span) * plotH;
    return [x, y] as const;
  });
  const linePath = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  const fillPath = linePath + ` L ${PAD_L + plotW} ${PAD_T + plotH} L ${PAD_L} ${PAD_T + plotH} Z`;
  const stroke = up ? "var(--up, #1f7a3a)" : "var(--down, #b53030)";

  // Y axis tick prices
  const mid = (min + max) / 2;
  const yTicks = [
    { v: max, y: PAD_T },
    { v: mid, y: PAD_T + plotH / 2 },
    { v: min, y: PAD_T + plotH },
  ];

  // X axis tick times — start, middle, end
  const lastIdx = points.length - 1;
  const midIdx = Math.floor(lastIdx / 2);
  const xTicks = [
    { idx: 0,        x: PAD_L },
    { idx: midIdx,   x: PAD_L + plotW * (midIdx / lastIdx) },
    { idx: lastIdx,  x: PAD_L + plotW },
  ];

  const fmtY = (v: number) => {
    if (v >= 1000) return v.toFixed(0);
    if (v >= 10)   return v.toFixed(2);
    return v.toFixed(3);
  };

  return (
    <svg className="tstock-chart-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id="stock-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stopColor={stroke} stopOpacity="0.16" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>

      {/* Y gridlines */}
      {yTicks.map((t, i) => (
        <line key={i}
          x1={PAD_L} x2={W - PAD_R}
          y1={t.y} y2={t.y}
          stroke="rgba(20,18,24,0.08)" strokeWidth="0.6"
          strokeDasharray={i === 1 ? "2 3" : "none"}
        />
      ))}

      {/* Fill + line */}
      <path d={fillPath} fill="url(#stock-fill)" />
      <path d={linePath} fill="none" stroke={stroke} strokeWidth="1.4" strokeLinejoin="round" strokeLinecap="round" />

      {/* Y axis labels (price) */}
      {yTicks.map((t, i) => (
        <text key={i} className="tstock-chart-tick"
          x={PAD_L - 4} y={t.y + 3}
          textAnchor="end"
        >
          {fmtY(t.v)}
        </text>
      ))}

      {/* X axis labels (time) */}
      {xTicks.map((t, i) => (
        <text key={i} className="tstock-chart-tick"
          x={t.x} y={H - 4}
          textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
        >
          {fmtTickFor(rangeKey, points[t.idx]?.ts ?? "")}
        </text>
      ))}
    </svg>
  );
}

// Deterministic, LLM-free portfolio read — the always-available fallback. Top
// mover, laggard, breadth, and an overall sentiment word, from whatever quotes
// resolved. Unpriced holdings are counted, never hidden.
function localSummary(quotes: Quote[]): string {
  const priced = quotes.filter(
    (q) => !q.error && q.price !== null && q.change_pct !== null
  ) as Array<Quote & { change_pct: number }>;
  const missing = quotes.length - priced.length;
  if (priced.length === 0) {
    return `No live prices for your ${quotes.length} holding${quotes.length === 1 ? "" : "s"} right now.`;
  }
  const up = priced.filter((q) => q.change_pct > 0).length;
  const down = priced.filter((q) => q.change_pct < 0).length;
  const sorted = [...priced].sort((a, b) => b.change_pct - a.change_pct);
  const best = sorted[0], worst = sorted[sorted.length - 1];
  const avg = priced.reduce((s, q) => s + q.change_pct, 0) / priced.length;
  const mood = avg > 0.25 ? "bullish" : avg < -0.25 ? "bearish" : "mixed";
  const pct = (n: number) => `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
  let line = `Book looks ${mood} — ${up} up, ${down} down. ${best.symbol} leads (${pct(best.change_pct)})`;
  if (worst.symbol !== best.symbol) line += `, ${worst.symbol} lags (${pct(worst.change_pct)})`;
  line += ".";
  if (missing > 0) line += ` ${missing} without a live quote.`;
  return line;
}

export default function StockTile() {
  const [symbols, setSymbols] = useState<string[]>(() => loadSymbols());
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [quotesError, setQuotesError] = useState<string | null>(null);
  const [quotesLoading, setQuotesLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchIdx, setSearchIdx] = useState(0);
  const searchAbortRef = useRef<AbortController | null>(null);

  const [selected, setSelected] = useState<string | null>(null);
  const [range, setRange] = useState<StockRange>(() => loadRange());
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // Holdings (watchlist) vs Movers (day's top gainers/losers, same yfinance source).
  const [view, setView] = useState<PortfolioView>("holdings");
  const [gainers, setGainers] = useState<Mover[]>([]);
  const [losers, setLosers] = useState<Mover[]>([]);
  const [moversLoading, setMoversLoading] = useState(false);
  const [moversError, setMoversError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  useEffect(() => { saveSymbols(symbols); }, [symbols]);
  useEffect(() => { saveRange(range); }, [range]);

  // First symbol becomes the default selection.
  useEffect(() => {
    if (selected === null && symbols.length > 0) setSelected(symbols[0]);
    if (selected && !symbols.includes(selected)) setSelected(symbols[0] ?? null);
  }, [symbols, selected]);

  // Poll quotes every 60s (backend caches 60s anyway).
  useEffect(() => {
    if (symbols.length === 0) { setQuotes([]); setQuotesLoading(false); return; }
    const load = async () => {
      try {
        const r = await getStocks(symbols);
        if (!mountedRef.current) return;
        setQuotes(r.quotes);
        setQuotesError(null);
      } catch (e) {
        if (mountedRef.current) setQuotesError(e instanceof Error ? e.message : String(e));
      } finally {
        if (mountedRef.current) setQuotesLoading(false);
      }
    };
    setQuotesLoading(true);
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [symbols]);

  // Fetch history when selection or range changes.
  useEffect(() => {
    if (!selected) { setHistory([]); return; }
    let alive = true;
    setHistoryLoading(true);
    setHistoryError(null);
    getStockHistory(selected, range)
      .then((r) => { if (alive) setHistory(r.points); })
      .catch((e) => { if (alive) setHistoryError(String(e?.message ?? e)); })
      .finally(() => { if (alive) setHistoryLoading(false); });
    return () => { alive = false; };
  }, [selected, range]);

  // Portfolio summary — ALWAYS shows something. A deterministic local line is
  // set immediately (top mover / laggard / breadth / sentiment); the LLM line
  // is an upgrade layered on top when a key is present. No blank, no scary
  // error when OpenRouter is absent — the local read stands.
  useEffect(() => {
    if (quotes.length === 0) { setSummary(null); setSummaryError(null); return; }
    setSummary(localSummary(quotes));   // immediate, deterministic
    setSummaryError(null);
    const ready = quotes.filter((q) => !q.error && q.price !== null && q.change_pct !== null);
    if (ready.length === 0) return;     // nothing to upgrade with; local stands
    let alive = true;
    setSummaryLoading(true);
    getStockSummary(ready)
      .then((r) => { if (alive && r.line && r.line.trim()) setSummary(r.line); })
      .catch(() => { /* keep the local line — the LLM is optional */ })
      .finally(() => { if (alive) setSummaryLoading(false); });
    return () => { alive = false; };
  }, [quotes]);

  // Movers: fetch the day's top gainers/losers only while that view is active.
  // Backend caches 2min; we refresh on a 2min poll. Fails closed (clean error).
  useEffect(() => {
    if (view !== "movers") return;
    let alive = true;
    const load = () => {
      setMoversLoading(true);
      getStockMovers()
        .then((r) => { if (!alive) return; setGainers(r.gainers); setLosers(r.losers); setMoversError(null); })
        .catch((e) => { if (alive) setMoversError(e instanceof Error ? e.message : String(e)); })
        .finally(() => { if (alive) setMoversLoading(false); });
    };
    load();
    const id = setInterval(load, 120_000);
    return () => { alive = false; clearInterval(id); };
  }, [view]);

  // Debounced typeahead: fire a backend search ~250ms after the user stops
  // typing. Cancels in-flight previous searches.
  useEffect(() => {
    const q = draft.trim();
    if (q.length < 1) {
      setSearchResults([]);
      setSearchOpen(false);
      return;
    }
    const t = window.setTimeout(async () => {
      searchAbortRef.current?.abort();
      const ctrl = new AbortController();
      searchAbortRef.current = ctrl;
      try {
        const r = await searchStocks(q, ctrl.signal);
        setSearchResults(r.results);
        setSearchOpen(r.results.length > 0);
        setSearchIdx(0);
      } catch (e) {
        if ((e as { name?: string })?.name === "AbortError") return;
        // Quiet — typeahead failures shouldn't shout. Just hide the popup.
        setSearchResults([]);
        setSearchOpen(false);
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [draft]);

  const addSymbol = (sym: string) => {
    const t = sym.trim().toUpperCase();
    if (!t) return;
    setDraft("");
    setSearchOpen(false);
    setSearchResults([]);
    if (symbols.includes(t)) {
      setSelected(t);
      return;
    }
    if (symbols.length >= 12) return;
    setSymbols((prev) => [...prev, t]);
    setSelected(t);
  };

  const remove = (sym: string) => {
    setSymbols((prev) => prev.filter((s) => s !== sym));
  };

  const fmt = (n: number | null | undefined, digits = 2) =>
    (n === null || n === undefined) ? "—" : n.toFixed(digits);

  // Render rows from symbols (not from quotes), so newly-added tickers show
  // immediately with a "…" placeholder instead of vanishing until the fetch
  // returns.
  const rows = useMemo(() => symbols.map((sym) => {
    const q = quotes.find((x) => x.symbol === sym);
    return { sym, quote: q ?? null };
  }), [symbols, quotes]);

  const selectedQuote = useMemo(() => quotes.find((q) => q.symbol === selected), [quotes, selected]);
  const histUp = (selectedQuote?.change ?? 0) >= 0;

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />My Portfolio</span>
        <div
          className="tstock-view-toggle no-drag"
          onMouseDown={(e) => e.stopPropagation()}
          role="tablist"
          aria-label="Portfolio view"
        >
          <button
            type="button"
            className={`tstock-view-btn ${view === "holdings" ? "on" : ""}`}
            onClick={() => setView("holdings")}
            role="tab"
            aria-selected={view === "holdings"}
          >
            Holdings
          </button>
          <button
            type="button"
            className={`tstock-view-btn ${view === "movers" ? "on" : ""}`}
            onClick={() => setView("movers")}
            role="tab"
            aria-selected={view === "movers"}
          >
            Movers
          </button>
        </div>
      </div>
      <div
        className="tile-body tstock-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {/* Chart header */}
        <div className="tstock-chart-head">
          <div className="tstock-chart-sym">
            <strong>{selectedQuote?.symbol ?? selected ?? "—"}</strong>
            <span className="tstock-chart-price">
              {selectedQuote ? fmt(selectedQuote.price) : ""}
            </span>
            {selectedQuote?.change_pct !== undefined && selectedQuote.change_pct !== null && (
              <span className={`tstock-chart-chg ${histUp ? "up" : "down"}`}>
                {selectedQuote.change_pct > 0 ? "+" : ""}{fmt(selectedQuote.change_pct)}%
              </span>
            )}
          </div>
          <select
            className="tstock-range no-drag"
            value={range}
            onChange={(e) => setRange(e.target.value as StockRange)}
            onMouseDown={(e) => e.stopPropagation()}
            title="Chart range"
          >
            {RANGES.map((r) => (
              <option key={r.key} value={r.key}>{r.label}</option>
            ))}
          </select>
        </div>

        {/* Chart */}
        <div className="tstock-chart-body">
          {historyLoading && <div className="tstock-spark-empty">loading…</div>}
          {historyError && <div className="tstock-spark-err">{historyError}</div>}
          {!historyLoading && !historyError && (
            <Chart points={history} up={histUp} rangeKey={range} />
          )}
        </div>

        {view === "holdings" && (
        <>
        {/* Add ticker — search by company name or symbol */}
        <div className="tstock-add no-drag" onMouseDown={(e) => e.stopPropagation()}>
          <input
            className="tstock-add-input no-drag"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={() => { if (searchResults.length) setSearchOpen(true); }}
            onBlur={() => window.setTimeout(() => setSearchOpen(false), 180)}
            onKeyDown={(e) => {
              if (searchOpen && searchResults.length > 0) {
                if (e.key === "ArrowDown") { e.preventDefault(); setSearchIdx((i) => (i + 1) % searchResults.length); return; }
                if (e.key === "ArrowUp")   { e.preventDefault(); setSearchIdx((i) => (i - 1 + searchResults.length) % searchResults.length); return; }
                if (e.key === "Enter")     { e.preventDefault(); addSymbol(searchResults[searchIdx].symbol); return; }
                if (e.key === "Escape")    { setSearchOpen(false); return; }
              }
              if (e.key === "Enter") { e.preventDefault(); addSymbol(draft); }
            }}
            placeholder="search a company or ticker (e.g. nvidia, TSLA)"
            spellCheck={false}
          />
          {searchOpen && searchResults.length > 0 && (
            <div className="tstock-search-pop">
              {searchResults.map((r, i) => (
                <button
                  type="button"
                  key={`${r.symbol}-${i}`}
                  className={`tstock-search-item ${i === searchIdx ? "active" : ""}`}
                  onMouseDown={(e) => { e.preventDefault(); addSymbol(r.symbol); }}
                  onMouseEnter={() => setSearchIdx(i)}
                >
                  <span className="tstock-search-sym">{r.symbol}</span>
                  <span className="tstock-search-name">{r.name}</span>
                  {r.exchange && <span className="tstock-search-exch">{r.exchange}</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Watchlist — rendered from symbols, not quotes, so adds show now. */}
        {quotesError && <div className="tstock-status err">{quotesError}</div>}
        <ul className="tstock-list">
          {rows.map(({ sym, quote }) => {
            const up   = quote && (quote.change ?? 0) > 0;
            const down = quote && (quote.change ?? 0) < 0;
            const isSel = sym === selected;
            const loading = !quote && quotesLoading;
            return (
              <li
                className={`tstock-row ${isSel ? "selected" : ""}`}
                key={sym}
                onClick={() => setSelected(sym)}
              >
                <span className="tstock-sym">{sym}</span>
                <span className="tstock-price">
                  {!quote && loading ? <span className="tstock-row-load">…</span>
                    : (quote?.error || (quote && quote.price === null))
                      ? <span className="tstock-noquote" title={quote?.error || "no live quote for this ticker"}>no quote</span>
                    : fmt(quote?.price)}
                </span>
                <span className={`tstock-chg ${up ? "up" : down ? "down" : ""}`}>
                  {!quote ? "" :
                    (quote.change_pct === null || quote.change_pct === undefined)
                      ? ""
                      : `${quote.change_pct > 0 ? "+" : ""}${fmt(quote.change_pct)}%`}
                </span>
                <button
                  type="button"
                  className="tstock-rm no-drag"
                  onClick={(e) => { e.stopPropagation(); remove(sym); }}
                  onMouseDown={(e) => e.stopPropagation()}
                  title="Remove"
                  aria-label={`Remove ${sym}`}
                >
                  <svg viewBox="0 0 10 10" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1.2">
                    <path d="M2 2 L8 8 M8 2 L2 8" />
                  </svg>
                </button>
              </li>
            );
          })}
        </ul>

        {/* Market-sentiment caption — the analyst's opening line. Its own row,
            divided from the tickers; emphasis markers stripped (the row is
            already styled italic) so no literal asterisks leak through. */}
        <div className="tstock-footer">
          {summaryLoading && !summary && <span className="tstock-footer-line muted">Nova is reading the tape…</span>}
          {summaryError && !summary && <span className="tstock-footer-line err">{summaryError}</span>}
          {summary && <span className="tstock-footer-line">{summary.replace(/[*_`]+/g, "").trim()}</span>}
        </div>
        </>
        )}

        {view === "movers" && (
          <div className="tstock-movers">
            {moversLoading && gainers.length === 0 && losers.length === 0 && (
              <div className="tstock-status">loading…</div>
            )}
            {moversError && gainers.length === 0 && losers.length === 0 && (
              <div className="tstock-status err">Movers unavailable</div>
            )}
            {(gainers.length > 0 || losers.length > 0) && (
              <>
                <MoversList title="Gainers" rows={gainers} onPick={setSelected} fmt={fmt} />
                <MoversList title="Losers"  rows={losers}  onPick={setSelected} fmt={fmt} />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function MoversList({
  title, rows, onPick, fmt,
}: {
  title: string;
  rows: Mover[];
  onPick: (sym: string) => void;
  fmt: (n: number | null | undefined, digits?: number) => string;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="tstock-movers-group">
      <div className="tstock-movers-h">{title}</div>
      <ul className="tstock-list">
        {rows.map((m) => {
          const up = (m.change_pct ?? 0) > 0;
          const down = (m.change_pct ?? 0) < 0;
          return (
            <li className="tstock-row" key={m.symbol} onClick={() => onPick(m.symbol)} title={m.name ?? m.symbol}>
              <span className="tstock-sym">{m.symbol}</span>
              <span className="tstock-price">{fmt(m.price)}</span>
              <span className={`tstock-chg ${up ? "up" : down ? "down" : ""}`}>
                {m.change_pct === null || m.change_pct === undefined
                  ? ""
                  : `${m.change_pct > 0 ? "+" : ""}${fmt(m.change_pct)}%`}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
