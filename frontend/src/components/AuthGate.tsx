// The auth wall. Renders before anything else when the device isn't
// authenticated: first-run setup (set the master password) or login
// (name this device + password). Calm, matches the landing aesthetic.
// Themes in both light and dark via CSS variables.

import { useState } from "react";
import { useAuth } from "../state/AuthContext";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { phase } = useAuth();

  if (phase === "loading") {
    return (
      <div className="authgate">
        <div className="authgate-card">
          <div className="authgate-loading">Connecting…</div>
        </div>
      </div>
    );
  }
  if (phase === "unreachable") return <UnreachableScreen />;
  if (phase === "setup") return <SetupScreen />;
  if (phase === "login") return <LoginScreen />;
  return <>{children}<GuestNotice /></>;
}

/** Always visible in a guest session, so nobody mistakes a public instance
 *  for somewhere their work is kept. */
function GuestNotice() {
  const { guest, logout } = useAuth();
  const [busy, setBusy] = useState(false);
  if (!guest) return null;
  return (
    <div className="guest-notice" role="status">
      <span>Guest session. Everything here is deleted when you close this tab.</span>
      <button
        className="guest-notice-btn"
        disabled={busy}
        onClick={async () => { setBusy(true); await logout(); }}
      >
        {busy ? "Clearing…" : "Clear now"}
      </button>
    </div>
  );
}

function UnreachableScreen() {
  const { refresh, guestError } = useAuth();
  const [busy, setBusy] = useState(false);

  const retry = async () => {
    setBusy(true);
    try { await refresh(); }
    finally { setBusy(false); }
  };

  return (
    <div className="authgate">
      <div className="authgate-card">
        <div className="authgate-mark">∞</div>
        <h1 className="authgate-title">
          {guestError ? "Could not start a session." : "Cannot reach the server."}
        </h1>
        <p className="authgate-sub">
          {guestError ?? "Check your connection and try again. If you run this instance yourself, make sure the backend is running."}
        </p>
        <button className="authgate-btn" onClick={retry} disabled={busy}>
          {busy ? "Retrying…" : "Retry"}
        </button>
      </div>
    </div>
  );
}

function SetupScreen() {
  const { setup, storeBackend, setupTokenRequired, setupAvailable } = useAuth();
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const unencrypted = storeBackend !== null && storeBackend !== "dpapi";

  const submit = async () => {
    setErr(null);
    if (pw.length < 6) { setErr("At least 6 characters."); return; }
    if (pw !== confirm) { setErr("Passwords don't match."); return; }
    if (setupTokenRequired && !token.trim()) { setErr("Setup token required."); return; }
    setBusy(true);
    try {
      await setup(pw, setupTokenRequired ? token.trim() : undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // First-run setup is unauthenticated by nature — there is no credential yet to
  // check. On a public URL that means whoever POSTs first owns the instance, so
  // the backend refuses unless the caller proves it is the operator. Say that,
  // rather than showing a form that will always fail.
  if (!setupAvailable) {
    return (
      <div className="authgate">
        <div className="authgate-card">
          <div className="authgate-mark">∞</div>
          <h1 className="authgate-title">Not claimable.</h1>
          <p className="authgate-sub">
            First-run setup is disabled on this deployment. Set <code>SETUP_TOKEN</code> in
            the environment, redeploy, and this screen will ask for it.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="authgate">
      <div className="authgate-card">
        <div className="authgate-mark">∞</div>
        <h1 className="authgate-title">Welcome to Infinity.</h1>
        <p className="authgate-sub">Set a master password. It protects access to your backend.</p>

        {unencrypted && (
          <div className="authgate-warn">
            Encryption unavailable ({storeBackend}) — the password hash will be stored unencrypted.
          </div>
        )}

        <input
          className="authgate-input"
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="Master password"
          autoFocus
          autoComplete="new-password"
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
        />
        <input
          className="authgate-input"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder="Confirm password"
          autoComplete="new-password"
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
        />

        {setupTokenRequired && (
          <input
            className="authgate-input"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Setup token"
            autoComplete="off"
            onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
          />
        )}

        {err && <div className="authgate-error">{err}</div>}

        <button
          className="authgate-btn"
          onClick={submit}
          disabled={busy || !pw || !confirm || (setupTokenRequired && !token.trim())}
        >
          {busy ? "Setting…" : "Set password"}
        </button>
      </div>
    </div>
  );
}

function LoginScreen() {
  const { login } = useAuth();
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    if (!name.trim()) { setErr("Name this device."); return; }
    if (!pw) { setErr("Enter the password."); return; }
    setBusy(true);
    try {
      await login(pw, name.trim());
    } catch (e) {
      // Backend errors are already generic ("Authentication failed").
      setErr(e instanceof Error ? e.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="authgate">
      <div className="authgate-card">
        <div className="authgate-mark">∞</div>
        <h1 className="authgate-title">Welcome to Infinity.</h1>
        <p className="authgate-sub">Sign this device in.</p>

        <input
          className="authgate-input"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Device name"
          autoFocus
          autoComplete="off"
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
        />
        <input
          className="authgate-input"
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="Password"
          autoComplete="current-password"
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
        />

        {err && <div className="authgate-error">{err}</div>}

        <button className="authgate-btn" onClick={submit} disabled={busy || !name.trim() || !pw}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </div>
    </div>
  );
}
