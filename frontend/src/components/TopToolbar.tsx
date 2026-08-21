// Fixed top-center whiteboard toolbar. Symmetric counterpart to the
// FixedBubble at the bottom: bottom = talk to Nova, top = act on the
// whiteboard.
//
// Anchored to the VIEWPORT (position: fixed) — pan/zoom of the canvas
// must not move it. Controls:
//   + : unified add menu (spawn tile / focus existing tile, attach file)
//   pen / eraser : real drawing modes routed to DrawingLayer
//   [color] [width] : appear only while pen or eraser is active
//
// Pen/eraser are mutually-exclusive toggles. The toolbar lights up its
// alive-border when a non-select tool is active, mirroring the bubble's
// focus-within identity.

import { useEffect, useRef, useState } from "react";
import { useTool } from "../state/ToolContext";
import { useCanvas } from "../state/CanvasContext";
import { useTiles, TILE_REGISTRY } from "../state/TilesContext";
import { useFiles } from "../state/FilesContext";
import {
  useDrawing, PEN_PRESETS, PEN_WIDTHS,
} from "../state/DrawingContext";

type SubMenu = "root" | "tiles";
type PopupKey = "add" | "color" | "width" | null;

const MOBILE_HIDDEN_KINDS = new Set<string>(["browser", "code", "terminal"]);

export default function TopToolbar() {
  const { tool, toggle } = useTool();
  const { viewportCenter } = useCanvas();
  const { spawnTile } = useTiles();
  const { openFile } = useFiles();
  const { penColor, setPenColor, penWidth, setPenWidth } = useDrawing();

  // Filter the Tiles submenu by connector availability. Spotify only
  // appears once the OAuth flow has completed; before that it isn't a
  // dead tile in the list — it just isn't there. On the PHONE shell we also
  // drop desktop-only kinds (browser/spotify/code/terminal/blueprint/detect)
  // that don't render in the mobile feed/focus — detect has its own camera FAB.
  const onPhone = typeof document !== "undefined" && !!document.querySelector(".mobile-shell.is-phone");
  const availableTiles = TILE_REGISTRY.filter((t) => {
    if (onPhone && MOBILE_HIDDEN_KINDS.has(t.id)) return false;
    return true;
  });

  const [popup, setPopup] = useState<PopupKey>(null);
  const [submenu, setSubmenu] = useState<SubMenu>("root");
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click + Esc.
  useEffect(() => {
    if (popup === null) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(e.target as Node)) {
        setPopup(null);
        setSubmenu("root");
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setPopup(null); setSubmenu("root"); }
    };
    window.addEventListener("mousedown", onDoc);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDoc);
      window.removeEventListener("keydown", onKey);
    };
  }, [popup]);

  const openAdd = () => {
    setPopup((p) => (p === "add" ? null : "add"));
    setSubmenu("root");
  };

  const onSpawnTile = (id: string) => {
    const p = viewportCenter();
    spawnTile(id, p);
    setPopup(null);
    setSubmenu("root");
  };

  const onAttach = async () => {
    setPopup(null);
    setSubmenu("root");
    const f = await openFile();
    if (f) {
      const p = viewportCenter();
      spawnTile(f.id, p);
    }
  };

  const showPenExtras = tool !== "select";

  return (
    <div
      className={`top-toolbar ${tool !== "select" ? "tool-active" : ""}`}
      ref={rootRef}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div className="tt-add-wrap">
        <button
          type="button"
          className={`tt-btn ${popup === "add" ? "on-ghost" : ""}`}
          onClick={openAdd}
          title="Add"
          aria-label="Add"
          aria-expanded={popup === "add"}
        >
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6">
            <path d="M8 3 V13 M3 8 H13" strokeLinecap="round" />
          </svg>
        </button>

        {popup === "add" && (
          <div className="tt-pop" role="menu">
            {submenu === "root" && (
              <>
                <div className="tt-pop-h">Add</div>
                <button type="button" className="tt-pop-item" onClick={() => setSubmenu("tiles")}>
                  <span className="tt-pop-item-glyph">
                    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3">
                      <rect x="2" y="2" width="5" height="5" rx="1" />
                      <rect x="9" y="2" width="5" height="5" rx="1" />
                      <rect x="2" y="9" width="5" height="5" rx="1" />
                      <rect x="9" y="9" width="5" height="5" rx="1" />
                    </svg>
                  </span>
                  <span className="tt-pop-item-text">
                    <span className="tt-pop-item-label">Add tile</span>
                    <span className="tt-pop-item-sub">Open a tile</span>
                  </span>
                </button>
                <button type="button" className="tt-pop-item" onClick={onAttach}>
                  <span className="tt-pop-item-glyph">
                    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3">
                      <path d="M10 4 L5.5 8.5 a2 2 0 0 0 2.8 2.8 L13 7" />
                      <path d="M11.5 5.5 L7 10" />
                    </svg>
                  </span>
                  <span className="tt-pop-item-text">
                    <span className="tt-pop-item-label">Attach file</span>
                    <span className="tt-pop-item-sub">Image, PDF, or doc</span>
                  </span>
                </button>
              </>
            )}
            {submenu === "tiles" && (
              <>
                <div className="tt-pop-h">
                  <button type="button" className="tt-pop-back" onClick={() => setSubmenu("root")} aria-label="Back">
                    <svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.4">
                      <path d="M8 2 L4 6 L8 10" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                  Tiles
                </div>
                {availableTiles.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className="tt-pop-item"
                    onClick={() => onSpawnTile(t.id)}
                  >
                    <span className="tt-pop-item-glyph">
                      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3">
                        <rect x="2.5" y="2.5" width="11" height="11" rx="2" />
                      </svg>
                    </span>
                    <span className="tt-pop-item-text">
                      <span className="tt-pop-item-label">{t.label}</span>
                      <span className="tt-pop-item-sub">{t.description}</span>
                    </span>
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      <span className="tt-divider" />

      <button
        type="button"
        className={`tt-btn ${tool === "pen" ? "on" : ""}`}
        onClick={() => toggle("pen")}
        title="Pen"
        aria-label="Pen"
        aria-pressed={tool === "pen"}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
          <path d="M2.5 13.5 L4 10 L11 3 L13 5 L6 12 L2.5 13.5 Z" strokeLinejoin="round" />
          <path d="M9.5 4.5 L11.5 6.5" />
        </svg>
      </button>

      <button
        type="button"
        className={`tt-btn ${tool === "eraser" ? "on" : ""}`}
        onClick={() => toggle("eraser")}
        title="Eraser"
        aria-label="Eraser"
        aria-pressed={tool === "eraser"}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
          <path d="M3.5 11 L8 6.5 L12 10.5 L9.5 13 L6 13 L3.5 11 Z" strokeLinejoin="round" />
          <path d="M8 6.5 L11 3.5 L13.5 6 L12 7.5" />
        </svg>
      </button>

      {showPenExtras && (
        <>
          <span className="tt-divider" />

          {/* Pen color */}
          <div className="tt-add-wrap">
            <button
              type="button"
              className={`tt-btn tt-color-btn ${popup === "color" ? "on-ghost" : ""}`}
              onClick={() => setPopup((p) => (p === "color" ? null : "color"))}
              title="Pen color"
              aria-label="Pen color"
              aria-expanded={popup === "color"}
            >
              <span className="tt-color-swatch" style={{ background: penColor }} />
            </button>
            {popup === "color" && (
              <div className="tt-pop tt-pop-narrow" role="menu">
                <div className="tt-pop-h">Color</div>
                <div className="tt-color-grid">
                  {PEN_PRESETS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      className={`tt-color-cell ${c.toLowerCase() === penColor.toLowerCase() ? "on" : ""}`}
                      onClick={() => { setPenColor(c); setPopup(null); }}
                      title={c}
                      aria-label={`Color ${c}`}
                    >
                      <span style={{ background: c }} />
                    </button>
                  ))}
                </div>
                <label className="tt-color-custom">
                  <span>Custom</span>
                  <input
                    type="color"
                    value={penColor}
                    onChange={(e) => setPenColor(e.target.value)}
                  />
                </label>
              </div>
            )}
          </div>

          {/* Pen width */}
          <div className="tt-add-wrap">
            <button
              type="button"
              className={`tt-btn ${popup === "width" ? "on-ghost" : ""}`}
              onClick={() => setPopup((p) => (p === "width" ? null : "width"))}
              title="Pen width"
              aria-label="Pen width"
              aria-expanded={popup === "width"}
            >
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeLinecap="round">
                <path d="M2 5.5 H14" strokeWidth="1" />
                <path d="M2 8.5 H14" strokeWidth="2.2" />
                <path d="M2 12 H14" strokeWidth="3.6" />
              </svg>
            </button>
            {popup === "width" && (
              <div className="tt-pop tt-pop-narrow" role="menu">
                <div className="tt-pop-h">Width</div>
                {PEN_WIDTHS.map((w) => (
                  <button
                    key={w.label}
                    type="button"
                    className={`tt-pop-item tt-width-item ${penWidth === w.value ? "on" : ""}`}
                    onClick={() => { setPenWidth(w.value); setPopup(null); }}
                  >
                    <span className="tt-pop-item-glyph tt-width-glyph">
                      <svg viewBox="0 0 24 8" width="22" height="8" fill="none" stroke="currentColor" strokeLinecap="round">
                        <path d="M2 4 H22" strokeWidth={w.value} />
                      </svg>
                    </span>
                    <span className="tt-pop-item-text">
                      <span className="tt-pop-item-label">{w.label}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
