// Memory master toggle. Persists to localStorage so the user's choice
// survives reload. When OFF, the frontend tells the backend to skip
// retrieval entirely on each /chat/stream call.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

const KEY = "infinity.memory.enabled.v1";

type Ctx = {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
};

function load(): boolean {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw === null) return true;
    return raw !== "false";
  } catch { return true; }
}

const MemoryCtx = createContext<Ctx | null>(null);

export function MemoryProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledRaw] = useState<boolean>(() => load());

  useEffect(() => {
    try { localStorage.setItem(KEY, enabled ? "true" : "false"); } catch { /* ignore */ }
  }, [enabled]);

  const setEnabled = useCallback((v: boolean) => setEnabledRaw(v), []);

  const value = useMemo<Ctx>(() => ({ enabled, setEnabled }), [enabled, setEnabled]);
  return <MemoryCtx.Provider value={value}>{children}</MemoryCtx.Provider>;
}

export function useMemorySettings(): Ctx {
  const v = useContext(MemoryCtx);
  if (!v) throw new Error("useMemorySettings outside MemoryProvider");
  return v;
}
