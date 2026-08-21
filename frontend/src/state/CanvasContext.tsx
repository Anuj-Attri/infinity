// Pan + zoom state for the whiteboard, plus screen<->canvas coordinate
// helpers. Lifted out of Dashboard so the TopToolbar, DrawingLayer, and
// TileGrid can all share the SAME transform and produce coordinates that
// line up across pan/zoom without any drift.
//
// canvas-space (the coords stored on tiles + strokes) is PRE-transform.
// A child at (cx, cy) inside `.dash-zoom` renders at screen coordinates:
//   (cx * zoom + pan.x + canvasRect.left, cy * zoom + pan.y + canvasRect.top)
// `screenToCanvas` is the inverse of that.

import {
  createContext, useCallback, useContext, useMemo, useRef, useState,
  type ReactNode, type RefObject,
} from "react";

export type Pan = { x: number; y: number };
export type Point = { x: number; y: number };

type Ctx = {
  pan: Pan;
  setPan: (p: Pan) => void;
  zoom: number;
  setZoom: (z: number | ((cur: number) => number)) => void;
  canvasRef: RefObject<HTMLDivElement | null>;
  /** Canvas-space coordinate of the current viewport center. Used as the
   *  default spawn point for new tiles + new strokes. */
  viewportCenter: () => Point;
  /** Convert a screen (clientX/Y) coord to canvas-space. */
  screenToCanvas: (clientX: number, clientY: number) => Point;
};

const CanvasCtx = createContext<Ctx | null>(null);

export function CanvasProvider({ children }: { children: ReactNode }) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [pan, setPan] = useState<Pan>({ x: 0, y: 0 });
  const [zoom, setZoomRaw] = useState<number>(1);

  const setZoom = useCallback((z: number | ((c: number) => number)) => {
    setZoomRaw((cur) => (typeof z === "function" ? (z as (c: number) => number)(cur) : z));
  }, []);

  const screenToCanvas = useCallback((clientX: number, clientY: number): Point => {
    const el = canvasRef.current;
    if (!el) return { x: clientX, y: clientY };
    const r = el.getBoundingClientRect();
    return {
      x: (clientX - r.left - pan.x) / zoom,
      y: (clientY - r.top  - pan.y) / zoom,
    };
  }, [pan.x, pan.y, zoom]);

  const viewportCenter = useCallback((): Point => {
    const el = canvasRef.current;
    if (!el) return { x: 0, y: 0 };
    const r = el.getBoundingClientRect();
    return {
      x: (r.width  / 2 - pan.x) / zoom,
      y: (r.height / 2 - pan.y) / zoom,
    };
  }, [pan.x, pan.y, zoom]);

  const value = useMemo<Ctx>(() => ({
    pan, setPan, zoom, setZoom, canvasRef, screenToCanvas, viewportCenter,
  }), [pan, zoom, setZoom, screenToCanvas, viewportCenter]);

  return <CanvasCtx.Provider value={value}>{children}</CanvasCtx.Provider>;
}

export function useCanvas(): Ctx {
  const v = useContext(CanvasCtx);
  if (!v) throw new Error("useCanvas called outside CanvasProvider");
  return v;
}
