// Browser tile — SERVER-SIDE reader.
//
// The old tile ran an OS-level Tauri child webview positioned over this tile's
// screen rect, with an <iframe> fallback. That drifted on scroll/resize, needed
// a window pool, and cannot exist in a hosted build at all. This version asks
// the backend to fetch + extract + sanitize, and renders the result inline.
//
// It is also the version the product wants: browsing that ENDS IN THE VAULT
// with a citation, instead of a Chrome clone in a box.
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { browseFetch, browseIngest, type BrowsePage } from "../../api";
import { TileIdContext } from "../../state/TilesContext";
import { consumePendingBrowserUrl } from "../../state/BrowserContext";

function normalizeUrl(raw: string): string {
  const s = (raw || "").trim();
  if (!s) return "";
  if (/^https?:\/\//i.test(s)) return s;
  if (/^[\w-]+(\.[\w-]+)+([/?#].*)?$/.test(s)) return `https://${s}`;
  return `https://duckduckgo.com/?q=${encodeURIComponent(s)}`;   // plain words → search
}

function hostOf(u: string): string {
  try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; }
}

export default function BrowserTile() {
  const [history, setHistory] = useState<string[]>([]);
  const [idx, setIdx] = useState(-1);
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState<BrowsePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const tileId = useContext(TileIdContext);

  // Spawned by openInBrowser() (a news headline, a chat link): the URL was
  // stashed against this tile id. Consume it once on mount.
  useEffect(() => {
    const pending = tileId ? consumePendingBrowserUrl(tileId) : null;
    if (pending) { setHistory([normalizeUrl(pending)]); setIdx(0); }
  }, [tileId]);

  const currentUrl = idx >= 0 ? history[idx] : "";
  const canBack = idx > 0;
  const canFwd = idx < history.length - 1;

  // Navigate: push onto history (truncating any forward entries).
  const go = useCallback((raw: string) => {
    const u = normalizeUrl(raw);
    if (!u) return;
    setHistory((prev) => [...prev.slice(0, idx + 1), u]);
    setIdx((i) => i + 1);
  }, [idx]);

  // Fetch whenever the current entry changes.
  useEffect(() => {
    if (!currentUrl) return;
    let cancelled = false;
    setLoading(true); setError(null); setSaveState("idle"); setSaveMsg(null);
    browseFetch(currentUrl)
      .then((p) => {
        if (cancelled) return;
        setPage(p);
        setDraft(p.final_url);
        bodyRef.current?.scrollTo({ top: 0 });
      })
      .catch((e) => { if (!cancelled) { setPage(null); setError(e instanceof Error ? e.message : String(e)); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [currentUrl]);

  // Links inside the article navigate the reader instead of the OS browser.
  const onBodyClick = useCallback((e: React.MouseEvent) => {
    const a = (e.target as HTMLElement)?.closest?.("a");
    if (!a) return;
    const href = a.getAttribute("href");
    if (!href) return;
    e.preventDefault();
    if (/^https?:\/\//i.test(href)) go(href);
  }, [go]);

  const save = useCallback(async () => {
    if (!page) return;
    setSaveState("saving"); setSaveMsg(null);
    try {
      const r = await browseIngest(page.final_url);
      setSaveState("saved");
      setSaveMsg(`Saved to Vault · ${r.chars.toLocaleString()} chars`);
    } catch (e) {
      setSaveState("error");
      setSaveMsg(e instanceof Error ? e.message : String(e));
    }
  }, [page]);

  const openExternal = useCallback(async () => {
    if (!page) return;
    // noopener is not boilerplate: without it the opened page keeps a live
    // window.opener handle back into this one.
    window.open(page.final_url, "_blank", "noopener,noreferrer");
  }, [page]);

  const meta = useMemo(() => hostOf(page?.final_url || currentUrl), [page, currentUrl]);

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title">Reader</span>
        <span className="tile-title-meta">{meta}</span>
      </div>

      <div
        className="tile-body tbrowser-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="tbrowser-nav">
          <button type="button" className="tbrowser-nav-btn" onClick={() => setIdx((i) => i - 1)}
                  disabled={!canBack} title="Back" aria-label="Back">
            <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M8 2 L3.5 6 L8 10" />
            </svg>
          </button>
          <button type="button" className="tbrowser-nav-btn" onClick={() => setIdx((i) => i + 1)}
                  disabled={!canFwd} title="Forward" aria-label="Forward">
            <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M4 2 L8.5 6 L4 10" />
            </svg>
          </button>
          <button type="button" className="tbrowser-nav-btn"
                  onClick={() => currentUrl && browseFetch(currentUrl).then(setPage).catch(() => {})}
                  disabled={!currentUrl} title="Reload" aria-label="Reload">
            <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.3">
              <path d="M2 6 A4 4 0 1 0 4.2 2.6 M2 2 V4.2 H4.2" />
            </svg>
          </button>
          <input
            type="text"
            className="tbrowser-url"
            value={draft}
            placeholder="Address, or words to search…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") go(draft); }}
            spellCheck={false}
          />
          {page && (
            <button type="button" className="tbrowser-nav-btn" onClick={openExternal}
                    title="Open in your browser" aria-label="Open in your browser">
              <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.3">
                <path d="M5 2 H2.5 A0.5 0.5 0 0 0 2 2.5 V9.5 A0.5 0.5 0 0 0 2.5 10 H9.5 A0.5 0.5 0 0 0 10 9.5 V7" />
                <path d="M7 2 H10 V5 M10 2 L5.5 6.5" strokeLinecap="round" />
              </svg>
            </button>
          )}
        </div>

        <div className="tbrowser-reader" ref={bodyRef} onClick={onBodyClick}>
          {loading && <div className="tbrowser-state">Fetching…</div>}

          {!loading && error && (
            <div className="tbrowser-state tbrowser-state-err">
              {error}
              <div className="tbrowser-state-sub">Try the address again, or open it in your browser.</div>
            </div>
          )}

          {!loading && !error && !page && (
            <div className="tbrowser-state">
              Type an address to read a page here.
              <div className="tbrowser-state-sub">
                Pages are fetched and cleaned on the backend, then can be saved
                straight into your Vault with the source kept as the citation.
              </div>
            </div>
          )}

          {!loading && !error && page && (
            <article className="tbrowser-article">
              <h1 className="tbrowser-h1">{page.title}</h1>
              <div className="tbrowser-byline">
                <span>{page.site}</span>
                <button
                  type="button"
                  className={`tbrowser-save${saveState === "saved" ? " is-saved" : ""}`}
                  onClick={save}
                  disabled={saveState === "saving" || saveState === "saved"}
                >
                  {saveState === "saving" ? "Saving…" : saveState === "saved" ? "In Vault" : "Save to Vault"}
                </button>
              </div>
              {saveMsg && (
                <div className={`tbrowser-savemsg${saveState === "error" ? " is-err" : ""}`}>{saveMsg}</div>
              )}
              {/* Server-sanitized: script/style/iframe/handlers are stripped
                  backend-side by an allowlist before this ever arrives. */}
              <div
                className="tbrowser-content"
                dangerouslySetInnerHTML={{ __html: page.html }}
              />
              {page.truncated && (
                <div className="tbrowser-state-sub">Page was long — showing the first part.</div>
              )}
            </article>
          )}
        </div>
      </div>
    </div>
  );
}
