// Persistent freehand strokes drawn on the whiteboard.
//
// Strokes live in CANVAS-space (pre-transform). The DrawingLayer renders
// them as <polyline>s inside `.dash-zoom`, so the same translate+scale
// transform that moves the tiles also moves the strokes. A stroke drawn
// next to a tile stays next to that tile under any pan + zoom.

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from "react";

export type Point = { x: number; y: number };
export type Stroke = {
  id: string;
  points: Point[];
  color: string;
  width: number;
};

const KEY        = "infinity.strokes.v1";
const COLOR_KEY  = "infinity.pen.color.v1";
const WIDTH_KEY  = "infinity.pen.width.v1";

export const PEN_COLOR_DEFAULT = "#5a4ca8";  // --accent-soft (void violet)
export const PEN_WIDTH_DEFAULT = 2.2;

export const PEN_PRESETS: string[] = [
  "#5a4ca8", // void violet (default)
  "#100e14", // ink black
  "#c8385d", // crimson
  "#d97c2e", // ember
  "#e2b53b", // amber
  "#2e8b57", // forest
  "#2563b8", // sapphire
  "#1f9aa6", // teal
];

export const PEN_WIDTHS: { label: string; value: number }[] = [
  { label: "thin",   value: 1.2 },
  { label: "medium", value: 2.2 },
  { label: "thick",  value: 4.4 },
];

type Ctx = {
  strokes: Stroke[];
  beginStroke: (p: Point, opts?: { color?: string; width?: number }) => string;
  appendPoint: (id: string, p: Point) => void;
  /** Remove every stroke whose path passes within `radius` (canvas units)
   *  of `p`. Returns true if anything was erased. */
  eraseAt: (p: Point, radius: number) => boolean;
  clear: () => void;

  // Current pen settings — both persist across reload. New strokes pick
  // these up automatically (DrawingLayer reads them when calling
  // beginStroke), and the chosen values are written into the stroke
  // itself so reloads keep each stroke's original look.
  penColor: string;
  setPenColor: (c: string) => void;
  penWidth: number;
  setPenWidth: (w: number) => void;
};

const DrawCtx = createContext<Ctx | null>(null);

function load(): Stroke[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((s) =>
      s && typeof s.id === "string" &&
      Array.isArray(s.points) && s.points.every((p: unknown) =>
        p && typeof (p as Point).x === "number" && typeof (p as Point).y === "number"
      )
    );
  } catch { return []; }
}

function loadColor(): string {
  try { const v = localStorage.getItem(COLOR_KEY); if (v && /^#[0-9a-f]{6}$/i.test(v)) return v; } catch { /* ignore */ }
  return PEN_COLOR_DEFAULT;
}
function loadWidth(): number {
  try {
    const raw = localStorage.getItem(WIDTH_KEY);
    if (raw) {
      const n = Number(raw);
      if (Number.isFinite(n) && n > 0 && n < 50) return n;
    }
  } catch { /* ignore */ }
  return PEN_WIDTH_DEFAULT;
}

export function DrawingProvider({ children }: { children: ReactNode }) {
  const [strokes, setStrokes] = useState<Stroke[]>(() => load());
  const [penColor, setPenColor] = useState<string>(() => loadColor());
  const [penWidth, setPenWidth] = useState<number>(() => loadWidth());
  const seqRef = useRef(0);

  // Debounced persistence — pen drag emits many small mutations.
  useEffect(() => {
    const h = window.setTimeout(() => {
      try { localStorage.setItem(KEY, JSON.stringify(strokes)); } catch { /* ignore */ }
    }, 250);
    return () => window.clearTimeout(h);
  }, [strokes]);
  useEffect(() => { try { localStorage.setItem(COLOR_KEY, penColor); } catch { /* ignore */ } }, [penColor]);
  useEffect(() => { try { localStorage.setItem(WIDTH_KEY, String(penWidth)); } catch { /* ignore */ } }, [penWidth]);

  const beginStroke = useCallback((p: Point, opts?: { color?: string; width?: number }) => {
    seqRef.current += 1;
    const id = `s_${Date.now().toString(36)}_${seqRef.current}`;
    const s: Stroke = {
      id, points: [p],
      color: opts?.color ?? PEN_COLOR_DEFAULT,
      width: opts?.width ?? PEN_WIDTH_DEFAULT,
    };
    setStrokes((cur) => [...cur, s]);
    return id;
  }, []);

  const appendPoint = useCallback((id: string, p: Point) => {
    setStrokes((cur) => {
      const i = cur.findIndex((s) => s.id === id);
      if (i < 0) return cur;
      const s = cur[i];
      const last = s.points[s.points.length - 1];
      if (last && Math.abs(last.x - p.x) < 0.5 && Math.abs(last.y - p.y) < 0.5) return cur;
      const next = cur.slice();
      next[i] = { ...s, points: [...s.points, p] };
      return next;
    });
  }, []);

  const eraseAt = useCallback((p: Point, radius: number): boolean => {
    const r2 = radius * radius;
    let hit = false;
    setStrokes((cur) => {
      const out: Stroke[] = [];
      for (const s of cur) {
        let near = false;
        for (let i = 0; i < s.points.length; i++) {
          const a = s.points[i];
          const dx = a.x - p.x, dy = a.y - p.y;
          if (dx * dx + dy * dy <= r2) { near = true; break; }
          if (i > 0) {
            // Segment midpoint check — bridges gaps between sparse points.
            const b = s.points[i - 1];
            const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
            const ddx = mx - p.x, ddy = my - p.y;
            if (ddx * ddx + ddy * ddy <= r2) { near = true; break; }
          }
        }
        if (near) hit = true;
        else out.push(s);
      }
      return hit ? out : cur;
    });
    return hit;
  }, []);

  const clear = useCallback(() => setStrokes([]), []);

  const value = useMemo<Ctx>(() => ({
    strokes, beginStroke, appendPoint, eraseAt, clear,
    penColor, setPenColor, penWidth, setPenWidth,
  }), [strokes, beginStroke, appendPoint, eraseAt, clear, penColor, penWidth]);

  return <DrawCtx.Provider value={value}>{children}</DrawCtx.Provider>;
}

export function useDrawing(): Ctx {
  const v = useContext(DrawCtx);
  if (!v) throw new Error("useDrawing called outside DrawingProvider");
  return v;
}
