// Model + no_think controls inside the bubble.
//   Left pill: click → small floating popup with search + grouped models
//   Right pill: Fast / Thinking toggle (local model only)

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useChatModels } from "../state/ChatModelsContext";
import { FALLBACK_MODELS, PROVIDER_LABEL, PROVIDER_ORDER } from "../config/models";
import type { ModelDef } from "../config/models";

// Detect dated variant suffix e.g. "-2026-03-17" → family id without the date
const DATE_SUFFIX_RE = /^(.+?)-(\d{4}-\d{2}-\d{2})$/;
function familyId(id: string): string {
  return DATE_SUFFIX_RE.exec(id)?.[1] ?? id;
}

type FamilyGroup = { famId: string; primary: ModelDef; variants: ModelDef[] };
type ProviderSection = { provider: string; label: string; families: FamilyGroup[] };

function buildSections(available: ModelDef[], q: string): ProviderSection[] {
  const lq = q.trim().toLowerCase();
  const filtered = lq
    ? available.filter((m) => m.label.toLowerCase().includes(lq) || m.id.toLowerCase().includes(lq))
    : available;

  return PROVIDER_ORDER
    .map((prov) => {
      const ms = filtered.filter((m) => m.provider === prov);
      if (!ms.length) return null;

      const famMap = new Map<string, ModelDef[]>();
      for (const m of ms) {
        const fid = familyId(m.id);
        if (!famMap.has(fid)) famMap.set(fid, []);
        famMap.get(fid)!.push(m);
      }

      const families: FamilyGroup[] = [];
      for (const [fid, models] of famMap) {
        const primary = models.find((m) => m.id === fid) ?? models[0];
        families.push({ famId: fid, primary, variants: models.filter((m) => m !== primary) });
      }
      return { provider: prov, label: PROVIDER_LABEL[prov] ?? prov, families };
    })
    .filter(Boolean) as ProviderSection[];
}

type Props = {
  modelId: string;
  setModelId: (id: string) => void;
  noThink: boolean;
  setNoThink: (v: boolean) => void;
  disabled?: boolean;
};

export default function ModelControls({ modelId, setModelId, noThink, setNoThink, disabled }: Props) {
  const { models } = useChatModels();
  const all = models.length ? models : FALLBACK_MODELS;
  const current = all.find((m) => m.id === modelId) ?? all[0];
  const supportsNT = !!current?.supportsNoThink;
  const available = all.filter((m) => m.available);

  const [open, setOpen]   = useState(false);
  const [query, setQuery] = useState("");
  const [focusIdx, setFocusIdx] = useState(-1);
  const [expandedFamilies, setExpandedFamilies] = useState<Set<string>>(new Set());

  const rootRef   = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef   = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setFocusIdx(-1);
  }, []);

  // Click-outside close
  useEffect(() => {
    if (!open) return;
    const onMouse = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close();
    };
    document.addEventListener("mousedown", onMouse);
    return () => document.removeEventListener("mousedown", onMouse);
  }, [open, close]);

  // Auto-focus search when popup opens
  useEffect(() => {
    if (open) {
      window.setTimeout(() => searchRef.current?.focus(), 20);
      setFocusIdx(-1);
    }
  }, [open]);

  const searching = query.trim().length > 0;
  const sections  = useMemo(() => buildSections(available, query), [available, query]);

  // Flat list of selectable models (for keyboard nav)
  const flatItems = useMemo<ModelDef[]>(() => {
    const items: ModelDef[] = [];
    for (const sec of sections) {
      for (const fam of sec.families) {
        items.push(fam.primary);
        if (fam.variants.length > 0 && (searching || expandedFamilies.has(fam.famId))) {
          items.push(...fam.variants);
        }
      }
    }
    return items;
  }, [sections, expandedFamilies, searching]);

  // Flat index lookup: model id → position in flatItems
  const flatIdxMap = useMemo<Map<string, number>>(() => {
    const m = new Map<string, number>();
    flatItems.forEach((item, i) => m.set(item.id, i));
    return m;
  }, [flatItems]);

  // Scroll focused item into view
  useEffect(() => {
    if (focusIdx < 0 || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-fi="${focusIdx}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [focusIdx]);

  const select = useCallback((id: string) => {
    setModelId(id);
    close();
  }, [setModelId, close]);

  const toggleFamily = (fid: string) => {
    setExpandedFamilies((prev) => {
      const next = new Set(prev);
      if (next.has(fid)) next.delete(fid); else next.add(fid);
      return next;
    });
  };

  const onSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFocusIdx((fi) => Math.min(fi + 1, flatItems.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setFocusIdx((fi) => Math.max(fi - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (focusIdx >= 0 && focusIdx < flatItems.length) select(flatItems[focusIdx].id);
    } else if (e.key === "Escape") {
      close();
    }
  };

  // Everything is OpenRouter now — the provider suffix on the pill is noise.
  // Show just the model name.
  return (
    <div className="mc-row" ref={rootRef}>
      {/* Model pill */}
      <button
        type="button"
        className={`mc-pill mc-pill-model ${open ? "mc-pill-open" : ""} ${available.length <= 1 ? "muted" : ""}`}
        onClick={() => { if (!disabled) setOpen((v) => !v); }}
        disabled={disabled}
        title="Switch model"
      >
        <span className="mc-dot mc-dot-model" />
        <span className="mc-label">{current?.label ?? "—"}</span>
        {available.length > 1 && (
          <span className={`mc-chevron ${open ? "mc-chevron-up" : ""}`}>▾</span>
        )}
      </button>

      {open && (
        <div className="mc-popup" role="dialog" aria-label="Select model">
          {/* Search input */}
          <div className="mc-search-wrap">
            <svg className="mc-search-icon" viewBox="0 0 14 14" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.4">
              <circle cx="6" cy="6" r="4.5" />
              <path d="M9.5 9.5 L12.5 12.5" strokeLinecap="round" />
            </svg>
            <input
              ref={searchRef}
              className="mc-search no-drag"
              placeholder="Search models…"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setFocusIdx(-1); }}
              onKeyDown={onSearchKeyDown}
              aria-label="Search models"
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          {/* Scrollable model list */}
          <div className="mc-list" ref={listRef} role="listbox">
            {sections.length === 0 && (
              <div className="mc-empty">No models match</div>
            )}
            {sections.map((sec) => (
              <div key={sec.provider} className="mc-group">
                <div className="mc-group-label">{sec.label}</div>
                {sec.families.map((fam) => {
                  const hasVariants = fam.variants.length > 0;
                  const expanded    = searching || expandedFamilies.has(fam.famId);
                  const pfi         = flatIdxMap.get(fam.primary.id) ?? -1;
                  return (
                    <div key={fam.famId} className="mc-family">
                      {/* Primary model row */}
                      <button
                        type="button"
                        role="option"
                        aria-selected={fam.primary.id === modelId}
                        data-fi={pfi}
                        className={[
                          "mc-popup-item",
                          fam.primary.id === modelId ? "mc-popup-item-active" : "",
                          pfi === focusIdx ? "mc-popup-item-focused" : "",
                        ].join(" ")}
                        onClick={() => select(fam.primary.id)}
                      >
                        <span className="mc-popup-name">{fam.primary.label}</span>
                        {fam.primary.id === modelId
                          ? <span className="mc-popup-check">✓</span>
                          : hasVariants && !expanded
                            ? <span className="mc-popup-variants-badge">{fam.variants.length + 1}</span>
                            : null}
                      </button>

                      {/* Expanded variant rows */}
                      {hasVariants && expanded && fam.variants.map((v) => {
                        const fi = flatIdxMap.get(v.id) ?? -1;
                        return (
                          <button
                            key={v.id}
                            type="button"
                            role="option"
                            aria-selected={v.id === modelId}
                            data-fi={fi}
                            className={[
                              "mc-popup-item mc-popup-variant",
                              v.id === modelId ? "mc-popup-item-active" : "",
                              fi === focusIdx ? "mc-popup-item-focused" : "",
                            ].join(" ")}
                            onClick={() => select(v.id)}
                          >
                            <span className="mc-popup-name">{v.label}</span>
                            {v.id === modelId && <span className="mc-popup-check">✓</span>}
                          </button>
                        );
                      })}

                      {/* Expander toggle — only when not searching */}
                      {hasVariants && !searching && (
                        <button type="button" className="mc-family-expander" onClick={() => toggleFamily(fam.famId)}>
                          {expanded
                            ? "▴ fewer versions"
                            : `▸ ${fam.variants.length} more version${fam.variants.length === 1 ? "" : "s"}`}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Fast / Thinking pill */}
      <button
        type="button"
        className={`mc-pill ${noThink ? "on" : ""} ${supportsNT ? "" : "muted"}`}
        onClick={() => supportsNT && setNoThink(!noThink)}
        disabled={disabled || !supportsNT}
        title={
          supportsNT
            ? (noThink ? "Fast mode — click for Thinking" : "Thinking mode — click for Fast")
            : `${current?.label ?? "This model"} doesn't use the Fast/Thinking toggle`
        }
      >
        <span className="mc-dot mc-dot-nt" />
        <span className="mc-label">{noThink ? "Fast" : "Thinking"}</span>
      </button>
    </div>
  );
}
