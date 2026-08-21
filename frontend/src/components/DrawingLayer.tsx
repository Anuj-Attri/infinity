// Whiteboard drawing layer.
//
// COORDINATE-SPACE CONTRACT — this has bitten us before:
//
//   The SVG that renders persisted strokes lives as a DIRECT child of
//   `.dash-canvas` (the element `canvasRef` points at) — NOT inside
//   `.dash-zoom`. It is `position: absolute; left: 0; top: 0;
//   width: 100%; height: 100%; overflow: visible`, with NO padding,
//   border, or other intermediate transforms between it and `.dash-canvas`.
//   This guarantees the SVG's coordinate origin coincides exactly with the
//   origin that `screenToCanvas` inverts from (canvasRef's rect top-left).
//
//   The SVG itself carries the same `translate(panX, panY) scale(zoom)`
//   transform that `.dash-zoom` carries for the tiles. With `transform-
//   origin: 0 0`, that puts a stroke point at canvas (cx, cy) at screen
//   position canvasRef.rect.left + panX + cx*zoom, which is exactly the
//   inverse of `screenToCanvas`. Ink lands under the cursor at any zoom
//   or pan, and active strokes coincide with persisted strokes.
//
// The pointer-capture overlay is also a direct child of `.dash-canvas`
// (sibling of the SVG) with no transform — it covers the full visible
// canvas in screen space and uses `screenToCanvas` to translate each
// pointer event into the same canvas-space the strokes live in.

import { useRef } from "react";
import { useCanvas } from "../state/CanvasContext";
import { useTool } from "../state/ToolContext";
import { useDrawing } from "../state/DrawingContext";

const ERASER_RADIUS_PX = 12;     // erase hit radius in SCREEN pixels;
                                 // divided by zoom to get canvas-space radius

export default function DrawingLayer() {
  const { tool } = useTool();
  const { screenToCanvas, pan, zoom } = useCanvas();
  const { strokes, beginStroke, appendPoint, eraseAt, penColor, penWidth } = useDrawing();
  const activeRef = useRef<string | null>(null);

  const onMouseDownGuard = (e: React.MouseEvent<HTMLDivElement>) => {
    if (tool === "select") return;
    e.preventDefault();
    e.stopPropagation();
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    if (tool === "select") return;
    e.preventDefault();
    e.stopPropagation();
    try { (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId); } catch { /* ignore */ }

    const r = ERASER_RADIUS_PX / zoom;
    const p0 = screenToCanvas(e.clientX, e.clientY);
    if (tool === "pen") {
      activeRef.current = beginStroke(p0, { color: penColor, width: penWidth });
    } else {
      eraseAt(p0, r);
    }

    const onMove = (em: PointerEvent) => {
      const p = screenToCanvas(em.clientX, em.clientY);
      if (tool === "pen") {
        const id = activeRef.current;
        if (id) appendPoint(id, p);
      } else if (tool === "eraser") {
        eraseAt(p, r);
      }
    };
    const onUp = () => {
      activeRef.current = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup",   onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup",   onUp);
    window.addEventListener("pointercancel", onUp);
  };

  const drawing = tool !== "select";
  const strokeTransform = `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`;

  return (
    <>
      <svg
        className="canvas-strokes"
        overflow="visible"
        style={{ transform: strokeTransform, transformOrigin: "0 0" }}
      >
        {strokes.map((s) => (
          <polyline
            key={s.id}
            points={s.points.map((p) => `${p.x},${p.y}`).join(" ")}
            stroke={s.color}
            strokeWidth={s.width}
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
            opacity={0.92}
          />
        ))}
      </svg>

      {drawing && (
        <div
          className={`canvas-draw-capture tool-${tool}`}
          onPointerDown={onPointerDown}
          onMouseDown={onMouseDownGuard}
        />
      )}
    </>
  );
}
