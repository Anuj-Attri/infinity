// Touch pan + pinch-zoom for the canvas.
//
// Ported from Mark 3's `src/mobile/useCanvasTouch.ts`. Without it the canvas is
// immovable on any touch device: Dashboard's only pan/zoom drivers are `wheel`
// and `mousedown/mousemove/mouseup`, all mouse-only, and the escape hatches are
// closed — `.dash-canvas` is `overflow: hidden` and index.html sets
// `user-scalable=no`. Zoom sat pinned at 1.0 and anything outside the opening
// viewport was unreachable, including the Browser tile whose default rect is at
// y:1120. Tiles themselves still dragged, so the app looked alive while the
// field around it was frozen.
//
//   1 finger  -> pan
//   2 fingers -> pinch-zoom anchored on the gesture midpoint, plus pan
//
// TWO ADAPTATIONS from Mark 3, both load-bearing here and not needed there,
// where this was mounted by a mobile-only shell that had no mouse handlers and
// no draggable tiles underneath it:
//
//   1. MOUSE POINTERS ARE IGNORED. A mouse fires pointerdown/move/up as well as
//      mousedown/move/up. Handling both would apply every desktop drag twice —
//      once here and once in Dashboard — and the canvas would pan at double
//      speed. Dashboard keeps the mouse; this owns touch and pen.
//
//   2. THE SAME TARGET EXCLUSIONS AS THE MOUSE PAN. TileGrid drags tiles with
//      pointer events, so without this a finger on a tile would drag the tile
//      AND pan the field under it at the same time.

import { useEffect, useRef } from "react";
import { useCanvas } from "./CanvasContext";

// Match Dashboard's ZOOM_STEPS bounds exactly. Two different clamps would mean
// pinch could reach a zoom the wheel refuses to leave.
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2.5;

const TAP_SLOP = 8;   // px of movement still counted as a tap, not a drag
const TAP_MS = 350;   // longest press still counted as a tap

/** Anything that owns its own gesture and must not also pan the field. */
const NOT_CANVAS = ".free-tile, .canvas-draw-capture, .fixed-bubble, .top-toolbar, .settings-panel, .palette";

type Opts = {
  /** False while a pen/eraser tool is active, so DrawingLayer owns the gesture. */
  enabled: boolean;
  onTap?: (clientX: number, clientY: number) => void;
};

export function useCanvasTouch(
  ref: React.RefObject<HTMLDivElement | null>,
  { enabled, onTap }: Opts,
) {
  const { pan, setPan, zoom, setZoom } = useCanvas();

  // Live values for listeners that are registered once and must never close
  // over stale state.
  const stateRef = useRef({ pan, zoom });
  stateRef.current = { pan, zoom };
  const onTapRef = useRef(onTap);
  onTapRef.current = onTap;
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const pointers = new Map<number, { x: number; y: number }>();
    let start: {
      single?: { x: number; y: number; pan: { x: number; y: number } };
      pinch?: { dist: number; mid: { x: number; y: number }; pan: { x: number; y: number }; zoom: number };
    } | null = null;
    let moved = false;
    let downAt = 0;
    let downPt = { x: 0, y: 0 };

    const onDown = (e: PointerEvent) => {
      if (!enabledRef.current) return;
      if (e.pointerType === "mouse") return;              // Dashboard owns the mouse
      const target = e.target as Element | null;
      if (target && target.closest(NOT_CANVAS)) return;   // the tile owns this drag

      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      try { el.setPointerCapture(e.pointerId); } catch { /* ignore */ }

      if (pointers.size === 1) {
        moved = false;
        downAt = performance.now();
        downPt = { x: e.clientX, y: e.clientY };
        start = { single: { x: e.clientX, y: e.clientY, pan: { ...stateRef.current.pan } } };
      } else if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        start = {
          pinch: {
            dist: Math.hypot(a.x - b.x, a.y - b.y),
            mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
            pan: { ...stateRef.current.pan },
            zoom: stateRef.current.zoom,
          },
        };
      }
    };

    const onMove = (e: PointerEvent) => {
      if (!enabledRef.current || !pointers.has(e.pointerId) || !start) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (pointers.size === 1 && start.single) {
        const dx = e.clientX - start.single.x;
        const dy = e.clientY - start.single.y;
        if (Math.hypot(dx, dy) > TAP_SLOP) moved = true;
        setPan({ x: start.single.pan.x + dx, y: start.single.pan.y + dy });
      } else if (pointers.size === 2 && start.pinch) {
        moved = true;
        const [a, b] = [...pointers.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        const z2 = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, start.pinch.zoom * (dist / start.pinch.dist)));
        // Anchor on the gesture midpoint so the content under the fingers stays
        // under the fingers — the same rule the wheel handler uses for the cursor.
        const ratio = z2 / start.pinch.zoom;
        const r = el.getBoundingClientRect();
        const mx = start.pinch.mid.x - r.left;
        const my = start.pinch.mid.y - r.top;
        setPan({
          x: mx - (mx - start.pinch.pan.x) * ratio,
          y: my - (my - start.pinch.pan.y) * ratio,
        });
        setZoom(z2);
      }
    };

    const onUp = (e: PointerEvent) => {
      if (!pointers.has(e.pointerId)) return;
      const wasSingle = pointers.size === 1;
      pointers.delete(e.pointerId);
      try { el.releasePointerCapture(e.pointerId); } catch { /* ignore */ }

      if (enabledRef.current && wasSingle && !moved && performance.now() - downAt < TAP_MS) {
        onTapRef.current?.(downPt.x, downPt.y);
      }

      if (pointers.size === 0) {
        start = null;
      } else if (pointers.size === 1) {
        // Pinch dropped to one finger: re-anchor the pan baseline, or the field
        // jumps by however far the lifted finger was from the remaining one.
        const [only] = [...pointers.values()];
        start = { single: { x: only.x, y: only.y, pan: { ...stateRef.current.pan } } };
        moved = true;
      }
    };

    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
    };
  }, [ref, setPan, setZoom]);
}
