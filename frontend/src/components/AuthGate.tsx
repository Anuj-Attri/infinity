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
  return <>{children}</>;
}

function UnreachableScreen() {
  const { refresh } = useAuth();
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
        <h1 className="authgate-title">Cannot reach backend.</h1>
        <p className="authgate-sub">
          Make sure Tailscale is connected and your home machine is awake.
        </p>
        <button className="authgate-btn" onClick={retry} disabled={busy}>
          {busy ? "Retrying…" : "Retry"}
        </button>
      </div>
    </div>
  );
}

function SetupScreen() {
  const { setup, storeBackend } = useAuth();
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const unencrypted = storeBackend !== null && storeBackend !== "dpapi";

  const submit = async () => {
    setErr(null);
    if (pw.length < 6) { setErr("At least 6 characters."); return; }
    if (pw !== confirm) { setErr("Passwords don't match."); return; }
    setBusy(true);
    try {
      await setup(pw);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

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

        {err && <div className="authgate-error">{err}</div>}

        <button className="authgate-btn" onClick={submit} disabled={busy || !pw || !confirm}>
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
