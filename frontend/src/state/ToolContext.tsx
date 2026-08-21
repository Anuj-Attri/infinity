// Current whiteboard tool. "select" is the default — pan + tile drag work
// as before. "pen" / "eraser" route pointer input to the DrawingLayer
// instead, and the Dashboard suppresses its own pan-on-mousedown.

import {
  createContext, useCallback, useContext, useMemo, useState,
  type ReactNode,
} from "react";

export type Tool = "select" | "pen" | "eraser";

type Ctx = {
  tool: Tool;
  setTool: (t: Tool) => void;
  /** Toggle a non-select tool — same tool clicked twice returns to select. */
  toggle: (t: Exclude<Tool, "select">) => void;
};

const ToolCtx = createContext<Ctx | null>(null);

export function ToolProvider({ children }: { children: ReactNode }) {
  const [tool, setTool] = useState<Tool>("select");
  const toggle = useCallback((t: Exclude<Tool, "select">) => {
    setTool((cur) => (cur === t ? "select" : t));
  }, []);
  const value = useMemo<Ctx>(() => ({ tool, setTool, toggle }), [tool, toggle]);
  return <ToolCtx.Provider value={value}>{children}</ToolCtx.Provider>;
}

export function useTool(): Ctx {
  const v = useContext(ToolCtx);
  if (!v) throw new Error("useTool called outside ToolProvider");
  return v;
}
