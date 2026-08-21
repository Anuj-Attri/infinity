// Ctrl+K command palette. Summon or focus any tile, tidy the canvas, close a
// tile — without touching the mouse.
//
// It is the first thing a new user presses (the landing footer advertises it),
// so it resolves against the LIVE tile registry rather than a hardcoded list:
// a tile added to TILE_REGISTRY shows up here automatically and can never drift
// out of sync.

import { useEffect, useMemo, useRef, useState } from "react";
import { useTiles } from "../state/TilesContext";

type Props = {
  open: boolean;
  onClose: () => void;
  /** Optional: jump to settings. Wired from App when it wants it. */
  onOpenSettings?: () => void;
};

type Cmd = {
  key: string;
  label: string;
  hint: string;
  group: string;
  run: () => void;
};

/** Subsequence match with a contiguity + word-start bonus. Small on purpose:
 *  a palette over ~25 commands does not need a fuzzy-search dependency. */
function score(query: string, text: string): number {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  const direct = t.indexOf(q);
  if (direct === 0) return 1000;            // prefix — always first
  if (direct > 0) return 500 - direct;      // substring
  let qi = 0, s = 0, streak = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      streak += 1;
      s += streak + (ti === 0 || t[ti - 1] === " " ? 3 : 0);
      qi += 1;
    } else {
      streak = 0;
    }
  }
  return qi === q.length ? s : -1;           // -1 → not a match
}

export default function CommandPalette({ open, onClose, onOpenSettings }: Props) {
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const { registry, spawnTile, focusTile, closeTile, arrangeTiles, isTileActive } = useTiles();

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSel(0);
    const t = setTimeout(() => inputRef.current?.focus(), 30);
    return () => clearTimeout(t);
  }, [open]);

  const commands = useMemo<Cmd[]>(() => {
    const out: Cmd[] = [];
    for (const t of registry) {
      const active = isTileActive(t.id);
      out.push({
        key: `open:${t.id}`,
        label: t.label,
        // Terse by house rule — one word carries it.
        hint: active ? "Focus" : "Open",
        group: "Tiles",
        run: () => (active ? focusTile(t.id) : spawnTile(t.id)),
      });
    }
    out.push({
      key: "layout:arrange",
      label: "Arrange tiles",
      hint: "Layout",
      group: "Canvas",
      run: () => arrangeTiles(),
    });
    for (const t of registry) {
      if (!isTileActive(t.id)) continue;
      out.push({
        key: `close:${t.id}`,
        label: `Close ${t.label}`,
        hint: "Close",
        group: "Canvas",
        run: () => closeTile(t.id),
      });
    }
    if (onOpenSettings) {
      out.push({
        key: "app:settings",
        label: "Settings",
        hint: "Open",
        group: "App",
        run: onOpenSettings,
      });
    }
    return out;
  }, [registry, isTileActive, spawnTile, focusTile, closeTile, arrangeTiles, onOpenSettings]);

  const results = useMemo(() => {
    return commands
      .map((c) => ({ c, s: Math.max(score(query, c.label), score(query, c.group) - 400) }))
      .filter((r) => r.s > -1)
      .sort((a, b) => b.s - a.s)
      .slice(0, 40)
      .map((r) => r.c);
  }, [commands, query]);

  // Selection must never point past the end when the filter narrows.
  useEffect(() => { setSel(0); }, [query]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setSel((i) => (results.length ? (i + 1) % results.length : 0));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSel((i) => (results.length ? (i - 1 + results.length) % results.length : 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const cmd = results[sel];
        if (cmd) {
          cmd.run();
          onClose();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, results, sel]);

  // Keep the highlighted row in view during arrow-key navigation.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${sel}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  if (!open) return null;

  let lastGroup = "";

  return (
    <div className="palette-overlay" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="palette-input-row">
          <span className="palette-search-glyph">
            <svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3">
              <circle cx="6" cy="6" r="4" /><path d="M9 9 L12 12" />
            </svg>
          </span>
          <input
            ref={inputRef}
            className="palette-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search · summon · run command…"
            aria-label="Command palette"
          />
          <span className="palette-mod">Ctrl + K</span>
        </div>

        <div className="palette-list" ref={listRef}>
          {results.length === 0 && (
            <div className="palette-empty">No match. Esc to close.</div>
          )}
          {results.map((c, i) => {
            const header = c.group !== lastGroup ? ((lastGroup = c.group), c.group) : null;
            return (
              <div key={c.key}>
                {header && <div className="palette-group">{header}</div>}
                <button
                  type="button"
                  data-idx={i}
                  className={`palette-row${i === sel ? " sel" : ""}`}
                  onMouseEnter={() => setSel(i)}
                  onClick={() => { c.run(); onClose(); }}
                >
                  <span className="palette-row-label">{c.label}</span>
                  <span className="palette-row-hint">{c.hint}</span>
                </button>
              </div>
            );
          })}
        </div>

        <div className="palette-foot">
          <span><b>↑↓</b> move</span>
          <span><b>↵</b> run</span>
          <span><b>Esc</b> close</span>
        </div>
      </div>
    </div>
  );
}
