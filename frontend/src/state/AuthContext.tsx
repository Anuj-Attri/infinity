// Device-auth state. Holds the long-lived Bearer token (localStorage) and
// drives the gate: first-run setup → login → authed. The token is attached
// to every backend call by api.ts (setAuthToken); a 401 anywhere clears it
// and bounces back to login (setUnauthorizedHandler).
//
// The token is a per-device bearer credential, not an API key — storing it
// in localStorage is intentional and matches the design. The strict
// "never in localStorage" rule applies to provider API keys, which never
// touch this layer.

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from "react";
import {
  getAuthStatus, getAuthMe, authLogin, authSetup, authLogout, authReset,
  setAuthToken, setUnauthorizedHandler,
} from "../api";

export type AuthPhase = "loading" | "setup" | "login" | "authed" | "unreachable";

type Ctx = {
  phase: AuthPhase;
  deviceName: string | null;
  /** "dpapi" | "identity" — surfaced so setup UI can warn on unencrypted fallback. */
  storeBackend: string | null;
  setup: (password: string) => Promise<void>;
  login: (password: string, deviceName: string) => Promise<void>;
  logout: () => Promise<void>;
  resetAll: () => Promise<void>;
  refresh: () => Promise<void>;
};

const TOKEN_KEY = "infinity.auth.token.v1";

function loadToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function saveToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* ignore */ }
}

const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<AuthPhase>("loading");
  const [deviceName, setDeviceName] = useState<string | null>(null);
  const [storeBackend, setStoreBackend] = useState<string | null>(null);

  // Keep api.ts's token in sync and expose a setter the 401 handler can use.
  const applyToken = useCallback((t: string | null, name: string | null) => {
    saveToken(t);
    setAuthToken(t);
    setDeviceName(name);
  }, []);

  // Bootstrap: status → (setup | validate stored token | login).
  const refresh = useCallback(async () => {
    setPhase("loading");
    let status;
    try {
      status = await getAuthStatus();
    } catch {
      // Backend unreachable (home machine off, Tailscale down, no network).
      // Show a clean retry screen rather than a login form that can't work.
      setPhase("unreachable");
      return;
    }
    setStoreBackend(status.store_backend);

    if (!status.password_set) {
      applyToken(null, null);
      setPhase("setup");
      return;
    }

    const stored = loadToken();
    if (!stored) {
      setPhase("login");
      return;
    }
    setAuthToken(stored);
    try {
      const me = await getAuthMe();
      if (me) {
        setDeviceName(me.device_name);
        setPhase("authed");
      } else {
        applyToken(null, null);
        setPhase("login");
      }
    } catch {
      setPhase("login");
    }
  }, [applyToken]);

  // Register the global 401 handler once. A revoked/invalid token anywhere
  // drops us straight back to the login screen.
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  useEffect(() => {
    setUnauthorizedHandler(() => {
      applyToken(null, null);
      setPhase("login");
    });
    return () => setUnauthorizedHandler(null);
  }, [applyToken]);

  useEffect(() => { void refresh(); }, [refresh]);

  const setup = useCallback(async (password: string) => {
    await authSetup(password);
    // Password now set; move to login (the device still needs a token).
    setPhase("login");
  }, []);

  const login = useCallback(async (password: string, name: string) => {
    const r = await authLogin(password, name);
    applyToken(r.token, r.device_name);
    setPhase("authed");
  }, [applyToken]);

  const logout = useCallback(async () => {
    try { await authLogout(); } catch { /* revoke best-effort */ }
    applyToken(null, null);
    setPhase("login");
  }, [applyToken]);

  const resetAll = useCallback(async () => {
    try { await authReset(); } catch { /* best-effort */ }
    applyToken(null, null);
    setStoreBackend((b) => b);
    setPhase("setup");
  }, [applyToken]);

  const value = useMemo<Ctx>(() => ({
    phase, deviceName, storeBackend, setup, login, logout, resetAll, refresh,
  }), [phase, deviceName, storeBackend, setup, login, logout, resetAll, refresh]);

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): Ctx {
  const v = useContext(AuthCtx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
