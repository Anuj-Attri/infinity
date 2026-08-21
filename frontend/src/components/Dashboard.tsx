// Whiteboard. Hosts the always-on Living Mesh underlay, the tile grid
// (with plain-wheel canvas zoom + click-drag panning on empty canvas),
// the drawing layer, the fixed input bubble at the bottom, and the
// fixed tools toolbar at the top.
//
// Zoom: plain wheel over empty canvas zooms; wheel over a tile passes
//       through to the tile's own scroll.
// Pan : mousedown on empty canvas (not on a tile) -> drag translates the
//       grid via a transform offset combined with the zoom scale.
//       Pan is SUPPRESSED when a non-select tool (pen/eraser) is active —
//       drawing takes precedence over canvas-pan.

import { useEffect, useRef } from "react";
import TileGrid from "./TileGrid";
import LivingMesh from "./LivingMesh";
import ErrorBoundary from "./ErrorBoundary";
import FixedBubble from "./FixedBubble";
import TopToolbar from "./TopToolbar";
import DrawingLayer from "./DrawingLayer";
import { useCanvas } from "../state/CanvasContext";
import { useTool } from "../state/ToolContext";
import { useCanvasTouch } from "../state/useCanvasTouch";

type Props = {
  visible: boolean;
  onOpenSettings: () => void;
  onOpenHistory?: () => void;
  onReturnToLanding: () => void;
};

// Curated zoom steps — snap to these instead of free fractional scales. Each is
// a "known" level (100% included + default) so the canvas re-rasterizes at a
// clean, predictable scale rather than drifting through the worst sub-pixel
// values that smear text. Fine enough to still feel like zooming.
const ZOOM_STEPS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3, 1.5, 1.75, 2.0, 2.5];
const MIN_ZOOM = ZOOM_STEPS[0];
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1];

/** Next/prev curated step from the current zoom (dir +1 = in, -1 = out). */
function nextZoomStep(current: number, dir: number): number {
  if (dir > 0) {
    for (const s of ZOOM_STEPS) if (s > current + 1e-4) return s;
    return MAX_ZOOM;
  }
  for (let i = ZOOM_STEPS.length - 1; i >= 0; i--) if (ZOOM_STEPS[i] < current - 1e-4) return ZOOM_STEPS[i];
  return MIN_ZOOM;
}

export default function Dashboard({ visible, onOpenSettings, onOpenHistory, onReturnToLanding }: Props) {
  const { pan, setPan, zoom, setZoom, canvasRef } = useCanvas();
  const { tool } = useTool();
  const toolRef = useRef(tool);
  toolRef.current = tool;

  // Touch pan + pinch-zoom. Dashboard's wheel/mouse handlers are mouse-only,
  // so without this the canvas cannot be moved or zoomed on any touch device.
  // Disabled while a pen/eraser tool is active, matching the mouse pan rule,
  // so DrawingLayer owns the gesture instead.
  useCanvasTouch(canvasRef, { enabled: tool === "select" });

  // Pan-drag bookkeeping.
  const dragRef = useRef<{
    active: boolean;
    startX: number;
    startY: number;
    origX: number;
    origY: number;
  }>({ active: false, startX: 0, startY: 0, origX: 0, origY: 0 });

  // Fresh pan/zoom for the wheel handler (read via refs so the listener never
  // re-subscribes and never sees stale values).
  const panRef = useRef(pan);
  panRef.current = pan;
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  // -------- wheel = ZOOM-TO-CURSOR (skips tiles → tile scroll preserved) -----
  // The canvas point under the cursor stays fixed as we scale (map-style zoom),
  // so zooming no longer drifts toward the top-left origin and the user can
  // always zoom out from any position. Math: screen = cx*zoom + pan, so to keep
  // the cursor's canvas point fixed: pan2 = m - (m - pan1) * (z2/z1).
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const target = e.target as Element | null;
      if (target && target.closest(".free-tile")) return;   // cursor over a tile → scroll the tile
      e.preventDefault();
      const z1 = zoomRef.current;
      const dir = e.deltaY > 0 ? -1 : 1;
      const z2 = nextZoomStep(z1, dir);   // snap to a crisp curated step
      if (z2 === z1) return;
      const r = el.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const p1 = panRef.current;
      const ratio = z2 / z1;
      setPan({ x: mx - (mx - p1.x) * ratio, y: my - (my - p1.y) * ratio });
      setZoom(z2);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [canvasRef, setZoom, setPan]);

  // -------- mousedown on empty canvas = pan (only in select mode) --------
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;

    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      if (toolRef.current !== "select") return; // pen/eraser owns drag
      const target = e.target as Element | null;
      if (target && target.closest(".free-tile")) return;
      if (target && target.closest(".canvas-draw-capture")) return;
      if (target && target.closest(".fixed-bubble, .top-toolbar, .settings-panel, .palette")) return;
      dragRef.current = {
        active: true,
        startX: e.clientX, startY: e.clientY,
        origX: pan.x,      origY: pan.y,
      };
      el.classList.add("panning");
      document.body.style.cursor = "grabbing";
      e.preventDefault();
    };

    const onMove = (e: MouseEvent) => {
      if (!dragRef.current.active) return;
      const dx = e.clientX - dragRef.current.startX;
      const dy = e.clientY - dragRef.current.startY;
      setPan({ x: dragRef.current.origX + dx, y: dragRef.current.origY + dy });
    };

    const onUp = () => {
      if (!dragRef.current.active) return;
      dragRef.current.active = false;
      el.classList.remove("panning");
      document.body.style.cursor = "";
    };

    el.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      el.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [canvasRef, pan.x, pan.y, setPan]);

  return (
    <div className={`dashboard ${visible ? "visible" : ""}`}>
      <LivingMesh />
      <div className={`dash-canvas tool-${tool}`} ref={canvasRef}>
        {/* DrawingLayer is a DIRECT child of `.dash-canvas` so its SVG
            shares the same coordinate origin that `screenToCanvas`
            inverts from. The SVG applies the pan+zoom transform itself
            instead of inheriting it from `.dash-zoom`. */}
        <DrawingLayer />
        <div
          className="dash-zoom"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "0 0",
            width: `${100 / zoom}%`,
            height: `${100 / zoom}%`,
          }}
        >
          <ErrorBoundary label="TileGrid">
            <TileGrid />
          </ErrorBoundary>
        </div>
      </div>
      {visible && <TopToolbar />}
      {visible && (
        <FixedBubble
          onOpenSettings={onOpenSettings}
          onOpenHistory={onOpenHistory}
          onReturnToLanding={onReturnToLanding}
        />
      )}
    </div>
  );
}
