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
  setAuthToken, setUnauthorizedHandler, guestStart, guestEndBeacon,
} from "../api";

export type AuthPhase = "loading" | "setup" | "login" | "authed" | "unreachable";

type Ctx = {
  phase: AuthPhase;
  /** True on a public instance: no sign-in, and this tab's data is deleted
   *  when it closes. */
  guest: boolean;
  /** Why the last guest session could not start, for the retry screen. */
  guestError: string | null;
  deviceName: string | null;
  /** True when this deployment requires an out-of-band SETUP_TOKEN to claim. */
  setupTokenRequired: boolean;
  /** False when first-run setup is refused for this caller entirely. */
  setupAvailable: boolean;
  /** "dpapi" | "identity" — surfaced so setup UI can warn on unencrypted fallback. */
  storeBackend: string | null;
  setup: (password: string, setupToken?: string) => Promise<void>;
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

// App storage keys. Guest mode clears them so nothing outlives the tab.
const APP_KEY_PREFIXES = ["infinity.", "supernova."];

/** Remove this app's keys from persistent browser storage.
 *
 *  The backend's /runtime-config.js normally makes this unnecessary by pointing
 *  the app at per-tab storage before any code runs. That script is only served
 *  when the backend also serves the page, so this is the fallback for a
 *  frontend hosted somewhere else. */
function clearPersistentAppStorage() {
  try {
    const w = window as unknown as { __INFINITY_GUEST__?: boolean };
    if (w.__INFINITY_GUEST__ === true) return;   // already per-tab
    const L = window.localStorage;
    for (let i = L.length - 1; i >= 0; i--) {
      const k = L.key(i);
      if (k && APP_KEY_PREFIXES.some((p) => k.startsWith(p))) L.removeItem(k);
    }
  } catch (e) {
    console.error("could not clear browser storage for the guest session", e);
  }
}

const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<AuthPhase>("loading");
  const [deviceName, setDeviceName] = useState<string | null>(null);
  const [storeBackend, setStoreBackend] = useState<string | null>(null);
  const [setupTokenRequired, setSetupTokenRequired] = useState(false);
  const [setupAvailable, setSetupAvailable] = useState(true);
  const [guest, setGuest] = useState(false);
  const [guestError, setGuestError] = useState<string | null>(null);
  const guestRef = useRef(false);

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
      // Backend unreachable (deploy down, no network, wrong API base URL).
      // Show a clean retry screen rather than a login form that can't work.
      setPhase("unreachable");
      return;
    }
    setStoreBackend(status.store_backend);
    setSetupTokenRequired(!!status.setup_token_required);
    // Absent on an older backend — treat as available so we do not lock out a
    // deployment that predates the flag.
    setSetupAvailable(status.setup_available !== false);

    if (status.guest_mode) {
      guestRef.current = true;
      setGuest(true);
      setGuestError(null);
      const stored = loadToken();
      if (stored) {
        // A reload in the same tab: keep the session if the server still has it.
        setAuthToken(stored);
        try {
          if (await getAuthMe()) {
            setDeviceName("Guest session");
            setPhase("authed");
            return;
          }
        } catch { /* expired or the server restarted; start a new one */ }
      }
      clearPersistentAppStorage();
      try {
        const g = await guestStart();
        applyToken(g.token, "Guest session");
        setPhase("authed");
      } catch (e) {
        applyToken(null, null);
        setGuestError(e instanceof Error ? e.message : String(e));
        setPhase("unreachable");
      }
      return;
    }

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
      // A guest has no login screen. The session expired or the server
      // restarted, and its data is gone either way, so start a fresh one.
      if (guestRef.current) { void refreshRef.current(); return; }
      setPhase("login");
    });
    return () => setUnauthorizedHandler(null);
  }, [applyToken]);

  useEffect(() => { void refresh(); }, [refresh]);

  // Closing the tab ends the guest session. pagehide also fires on a reload,
  // which is why the server waits a short grace period before deleting.
  useEffect(() => {
    if (!guest) return;
    const onHide = () => {
      const t = loadToken();
      if (t) guestEndBeacon(t);
      clearPersistentAppStorage();
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [guest]);

  const setup = useCallback(async (password: string, setupToken?: string) => {
    await authSetup(password, setupToken);
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
    if (guestRef.current) {
      // For a guest this is "clear my session": the server deleted it, so
      // wipe the tab's storage and start clean.
      try { window.sessionStorage.clear(); } catch { /* ignore */ }
      clearPersistentAppStorage();
      window.location.reload();
      return;
    }
    setPhase("login");
  }, [applyToken]);

  const resetAll = useCallback(async () => {
    try { await authReset(); } catch { /* best-effort */ }
    applyToken(null, null);
    setStoreBackend((b) => b);
    setPhase("setup");
  }, [applyToken]);

  const value = useMemo<Ctx>(() => ({
    phase, guest, guestError, deviceName, storeBackend, setupTokenRequired, setupAvailable,
    setup, login, logout, resetAll, refresh,
  }), [phase, guest, guestError, deviceName, storeBackend, setupTokenRequired, setupAvailable,
       setup, login, logout, resetAll, refresh]);

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): Ctx {
  const v = useContext(AuthCtx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
