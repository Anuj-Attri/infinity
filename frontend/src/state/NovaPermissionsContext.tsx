// Nova tool-use permission store.
//
// Persisted to localStorage so "allow always" grants survive refreshes.
// Backend (nova_permissions.json via DPAPI) is synced on mount and on
// every grant/revoke so grants also survive full app restarts.
//
// Default grants: read_tile is always-allowed (no side effects).
// Everything else requires per-call approval.

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from "react";
import { getNovaPerm, setNovaPerm, deleteNovaPerm } from "../api";

export type PermLevel = "always" | "ask";

type Ctx = {
  /** Returns the current permission level for a tool. */
  getPermission: (tool: string) => PermLevel;
  /** Grant "always" for a tool. */
  grantAlways: (tool: string) => Promise<void>;
  /** Revoke an always-grant, reverting to ask. */
  revoke: (tool: string) => Promise<void>;
  /** Flat map for the Settings UI. */
  grants: Record<string, PermLevel>;
};

const DEFAULTS: Record<string, PermLevel> = {
  read_tile: "always",
  read_tiles: "always",
  // Read-only market/news fetch (no mutation) — auto-run like reads. Writes /
  // spawns / arranges stay gated (not listed here => "ask").
  market_search: "always",
  // Read-only RAG retrieval over ingested documents — auto-run like reads.
  query_documents: "always",
  // Read-only Vault retrieval (the markdown second brain) — auto-run.
  query_vault: "always",
  // Read-only opt-in sensor readings — auto-run.
  read_sensors: "always",
};

const LS_KEY = "infinity.nova.permissions.v1";

const NovaPermCtx = createContext<Ctx | null>(null);

export function NovaPermissionsProvider({ children }: { children: ReactNode }) {
  const [grants, setGrants] = useState<Record<string, PermLevel>>(() => {
    try {
      const saved = localStorage.getItem(LS_KEY);
      if (saved) return { ...DEFAULTS, ...JSON.parse(saved) };
    } catch {}
    return { ...DEFAULTS };
  });

  // Sync from backend on mount (handles cross-restart persistence).
  useEffect(() => {
    getNovaPerm()
      .then((r) => {
        const remote: Record<string, PermLevel> = {};
        for (const [k, v] of Object.entries(r.permissions)) {
          if (v === "always") remote[k] = "always";
        }
        setGrants((cur) => {
          const merged = { ...DEFAULTS, ...cur, ...remote };
          try { localStorage.setItem(LS_KEY, JSON.stringify(merged)); } catch {}
          return merged;
        });
      })
      .catch(() => { /* backend unreachable — use local state */ });
  }, []);

  const persist = useCallback((next: Record<string, PermLevel>) => {
    setGrants(next);
    try { localStorage.setItem(LS_KEY, JSON.stringify(next)); } catch {}
  }, []);

  const grantAlways = useCallback(async (tool: string) => {
    persist({ ...grants, [tool]: "always" });
    try { await setNovaPerm(tool, "always"); } catch {}
  }, [grants, persist]);

  const revoke = useCallback(async (tool: string) => {
    // DEFAULTS tools can't be permanently revoked from "ask" in this session,
    // but the user can override the default for non-default tools.
    const next = { ...grants };
    delete next[tool];
    // Re-apply defaults for tools that have them.
    for (const [k, v] of Object.entries(DEFAULTS)) {
      if (!(k in next)) next[k] = v;
    }
    persist(next);
    try { await deleteNovaPerm(tool); } catch {}
  }, [grants, persist]);

  const getPermission = useCallback((tool: string): PermLevel => {
    return grants[tool] ?? "ask";
  }, [grants]);

  const value = useMemo<Ctx>(
    () => ({ getPermission, grantAlways, revoke, grants }),
    [getPermission, grantAlways, revoke, grants],
  );

  return <NovaPermCtx.Provider value={value}>{children}</NovaPermCtx.Provider>;
}

export function useNovaPermissions(): Ctx {
  const v = useContext(NovaPermCtx);
  if (!v) throw new Error("useNovaPermissions called outside NovaPermissionsProvider");
  return v;
}
