// Settings drawer.
//
// Grouped into tabs (see TABS below). Nothing here reads a raw key value, and
// nothing here reports on the product's own infrastructure — those are operator
// diagnostics and live in the Console tile.

import { useEffect, useRef, useState } from "react";
import {
  listMemories, createMemory, updateMemory, deleteMemory,
  connectChatProvider, disconnectChatProvider,
  getAuthDevices, revokeAuthDevice,
  getProfile, setProfile,
  type MemoryItem, type AuthDevice,
} from "../api";
import { useAuth } from "../state/AuthContext";
import { useSettings } from "../state/SettingsContext";
import { useMemorySettings } from "../state/MemoryContext";
import { useChatModels } from "../state/ChatModelsContext";
import McpConnectors from "./McpConnectors";
import { useNovaPermissions } from "../state/NovaPermissionsContext";
import { useNarrator } from "../state/NarratorContext";

type Props = {
  open: boolean;
  onClose: () => void;
};


// ── Groups ───────────────────────────────────────────────────────────────────
//
// This panel was thirteen sections in one scroll, with three of them called
// some variant of "Connectors" and two called "API Keys". Finding anything
// meant reading all of it.
//
// The grouping is by WHAT YOU CAME TO DO, not by which subsystem implements it:
// how the app looks, how Nova behaves, what it is plugged into, and who is
// signed in. Sections that only a developer would open (raw .env key masks,
// service status pills) are gone from here — they are diagnostics, and
// diagnostics belong in the operator Console.

type SettingsTab = "general" | "nova" | "connections" | "account" | "about";

const TABS: Array<{ id: SettingsTab; label: string }> = [
  { id: "general",     label: "General" },
  { id: "nova",        label: "Nova" },
  { id: "connections", label: "Connections" },
  { id: "account",     label: "Account" },
  { id: "about",       label: "About" },
];


export default function SettingsPanel({ open, onClose }: Props) {
  const [tab, setTab] = useState<SettingsTab>("general");
  const { settings, resolvedTheme, setHourFormat, setCustomSystemPrompt, resetCustomSystemPrompt, setTheme, accent, setAccent } = useSettings();
  const { enabled: memoryEnabled, setEnabled: setMemoryEnabled } = useMemorySettings();

  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [memError, setMemError] = useState<string | null>(null);
  const [memLoading, setMemLoading] = useState(false);
  const [draftText, setDraftText] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [memSearch, setMemSearch] = useState("");

  // One list, newest first.
  //
  // It used to be grouped under headings reading "profile" and "episodic" —
  // our storage vocabulary, printed at the user. The distinction is real
  // (profile is always-on baseline, episodic is recalled when it matches the
  // turn) but it is ours to manage, not theirs to sort by, and it told a reader
  // nothing they could act on.
  const memShownList = (() => {
    const q = memSearch.trim().toLowerCase();
    const filtered = q
      ? memories.filter((m) => m.text.toLowerCase().includes(q))
      : memories;
    return [...filtered].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  })();
  const memShown = memShownList.length;

  const refreshMemories = async () => {
    setMemLoading(true);
    setMemError(null);
    try {
      const r = await listMemories();
      setMemories(r.items);
    } catch (e) {
      setMemError(e instanceof Error ? e.message : String(e));
    } finally {
      setMemLoading(false);
    }
  };

  const addMemory = async () => {
    const t = draftText.trim();
    if (!t) return;
    try {
      // Always `profile`: a note someone types by hand is a standing fact
      // about themselves. `episodic` is Nova's own capture from a turn.
      await createMemory({ type: "profile", text: t });
      setDraftText("");
      refreshMemories();
    } catch (e) {
      setMemError(e instanceof Error ? e.message : String(e));
    }
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      await updateMemory(editingId, { text: editText });
      setEditingId(null);
      setEditText("");
      refreshMemories();
    } catch (e) {
      setMemError(e instanceof Error ? e.message : String(e));
    }
  };

  const removeMemory = async (id: string) => {
    try {
      await deleteMemory(id);
      refreshMemories();
    } catch (e) {
      setMemError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (!open) return;
    // Settings no longer reads /keys/status: the raw .env masks and service
    // status pills it fed are operator diagnostics, and the Console tile
    // already reports every one of them (which key is set, what it enables).
    refreshMemories();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="settings-overlay" onClick={onClose} />
      <aside className="settings-panel" role="dialog" aria-modal="true">
        <div className="settings-head">
          <span className="settings-title">Settings</span>
          <button className="settings-close" onClick={onClose} title="Close" aria-label="Close">
            <svg viewBox="0 0 14 14" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.2">
              <path d="M3 3 L11 11 M11 3 L3 11" />
            </svg>
          </button>
        </div>

        <nav className="settings-tabs" role="tablist" aria-label="Settings sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`settings-tab ${tab === t.id ? "on" : ""}`}
              onClick={() => setTab(t.id)}
            >{t.label}</button>
          ))}
        </nav>

        <div className="settings-body">
          {tab === "general" && <>
          <section className="settings-section">
            <div className="settings-section-h">Appearance</div>
            <div className="settings-row">
              <span>Theme</span>
              <div className="seg-control">
                {(["light", "dark", "system"] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={`seg-btn ${settings.theme === t ? "on" : ""}`}
                    onClick={() => setTheme(t)}
                  >
                    {t === "light" ? "Light" : t === "dark" ? "Dark" : "System"}
                  </button>
                ))}
              </div>
            </div>
            <div className="settings-row">
              <span>Accent</span>
              <div className="settings-accents">
                {(resolvedTheme === "dark"
                  ? [{ id: "default", label: "Void Violet", dot: "#8b7cf6" }, { id: "obsidian", label: "Obsidian", dot: "#0a0a0c" }]
                  : [{ id: "default", label: "Void Violet", dot: "#5a4ca8" }, { id: "sky", label: "Sky", dot: "#2563eb" }]
                ).map((a) => (
                  <button key={a.id} type="button"
                          className={`accent-swatch ${accent === a.id ? "on" : ""}`}
                          onClick={() => setAccent(a.id)}>
                    <span className="accent-dot" style={{ background: a.dot, boxShadow: a.id === "obsidian" ? "inset 0 0 0 1px rgba(255,255,255,.25)" : undefined }} />
                    {a.label}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-h">Date &amp; Time</div>
            <div className="settings-row">
              <span>Hour format</span>
              <div className="seg-control">
                <button
                  type="button"
                  className={`seg-btn ${settings.hourFormat === "12" ? "on" : ""}`}
                  onClick={() => setHourFormat("12")}
                >12-hour</button>
                <button
                  type="button"
                  className={`seg-btn ${settings.hourFormat === "24" ? "on" : ""}`}
                  onClick={() => setHourFormat("24")}
                >24-hour</button>
              </div>
            </div>
            <div className="settings-row">
              <span>Local timezone</span>
              <span className="settings-row-meta">{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
            </div>
          </section>
          </>}

          {tab === "account" && <>
          <section className="settings-section">
            <div className="settings-section-h">You</div>
            <ProfileSection open={open} />
          </section>

          <section className="settings-section">
            <div className="settings-section-h">Devices</div>
            <DevicesSection open={open} />
          </section>
          </>}

          {tab === "connections" && <>
          <section className="settings-section">
            <div className="settings-section-h">MCP servers</div>
            <div className="settings-stub">
              Connect an MCP server and its tools become callable by Nova —
              research, code review, or anything you host yourself. Nova asks
              before the first use of each tool.
            </div>
            <McpConnectors />
          </section>

          <section className="settings-section">
            {/* Bring-your-own keys for a DIRECT line to a provider. Optional:
                models work out of the box without any of this. The dispatch
                registry that powers sub-agents is ours and is not a user
                setting, so it is not shown — the user never supplies that key,
                and a "Connect / Disconnect" control over infrastructure they
                do not own is a button that can only break things. */}
            <div className="settings-section-h">Your own provider keys</div>
            <div className="settings-stub" style={{ marginBottom: 6 }}>
              Optional. Models already work without this — add a key only to
              bill a provider directly and unlock their full catalog.
            </div>
            <ChatProviderStoreBadge />
            <ChatProviderRow name="anthropic" label="Anthropic" placeholder="sk-ant-…" />
            <ChatProviderRow name="openai"    label="OpenAI"    placeholder="sk-…" />
            <ChatProviderRow name="gemini"    label="Gemini"    placeholder="AIza…" />
          </section>
          </>}

          {tab === "nova" && <>
          <section className="settings-section">
            <div className="settings-section-h">Memory</div>
            <div className="settings-row">
              <span>Enable memory</span>
              <div className="seg-control">
                <button
                  type="button"
                  className={`seg-btn ${memoryEnabled ? "on" : ""}`}
                  onClick={() => setMemoryEnabled(true)}
                >On</button>
                <button
                  type="button"
                  className={`seg-btn ${!memoryEnabled ? "on" : ""}`}
                  onClick={() => setMemoryEnabled(false)}
                >Off</button>
              </div>
            </div>
            <div className="settings-stub">
              {/* This used to end "Stored locally — never leaves this machine",
                  which was true of the desktop build and is false here: memories
                  live in the server's database and are sent to the model as
                  context on the turns that retrieve them. A privacy claim the
                  product cannot keep is worse than no claim, and the product rules
                  makes not overstating this a rule. */}
              {memoryEnabled
                ? "Nova retrieves relevant memories silently on each turn. Stored on the server for your account, and included in the model request when they are used."
                : "Memory retrieval paused. Stored memories are kept but not consulted."}
            </div>

            {/* No type picker. Something a person types here by hand is a
                standing fact about themselves — "I prefer concise replies" —
                which is what `profile` means. `episodic` is what Nova captures
                from a conversation on her own; asking the user to classify
                their own note into our two storage buckets was a question only
                someone who had read the schema could answer. */}
            <div className="mem-add">
              <input
                className="mem-add-input"
                type="text"
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addMemory(); } }}
                placeholder="Add a memory (e.g. 'I prefer concise replies')"
              />
              <button
                type="button"
                className="mem-add-btn"
                onClick={addMemory}
                disabled={!draftText.trim()}
              >Add</button>
            </div>

            {/* Search — filters by name/content. */}
            {memories.length > 0 && (
              <div className="mem-search">
                <svg className="mem-search-icon" viewBox="0 0 14 14" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.3">
                  <circle cx="6" cy="6" r="4.2" /><path d="M9.2 9.2 L12 12" strokeLinecap="round" />
                </svg>
                <input
                  className="mem-search-input"
                  type="text"
                  value={memSearch}
                  onChange={(e) => setMemSearch(e.target.value)}
                  placeholder="Search memories…"
                />
                {memSearch && (
                  <button type="button" className="mem-search-clear" onClick={() => setMemSearch("")} aria-label="Clear">×</button>
                )}
              </div>
            )}

            {memError && <div className="mem-error">{memError}</div>}
            {memLoading && memories.length === 0 && <div className="settings-stub">Loading memories…</div>}
            {memories.length === 0 && !memLoading && !memError && (
              <div className="settings-stub">No memories saved yet.</div>
            )}
            {memories.length > 0 && memShown === 0 && (
              <div className="settings-stub">No memories match “{memSearch}”.</div>
            )}

            {/* One flat list, newest first. The "auto" tag stays — that one IS
                worth knowing, because it marks something Nova decided to keep
                about you rather than something you typed. */}
            <div className="mem-cards">
              {memShownList.map((m) => (
                <div className={`mem-card ${editingId === m.id ? "editing" : ""}`} key={m.id}>
                  {editingId === m.id ? (
                    <div className="mem-card-edit">
                      <input
                        className="mem-edit-input"
                        type="text"
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { e.preventDefault(); saveEdit(); }
                          if (e.key === "Escape") { setEditingId(null); }
                        }}
                        autoFocus
                      />
                      <div className="mem-card-actions">
                        <button type="button" className="mem-btn" onClick={saveEdit}>save</button>
                        <button type="button" className="mem-btn mem-btn-ghost" onClick={() => setEditingId(null)}>cancel</button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <span className="mem-card-text">{m.text}</span>
                      <div className="mem-card-foot">
                        {m.source === "auto" && (
                          <span className="mem-tag" title="Nova saved this from something you said">auto</span>
                        )}
                        <div className="mem-card-actions">
                          <button
                            type="button"
                            className="mem-btn mem-btn-ghost"
                            onClick={() => { setEditingId(m.id); setEditText(m.text); }}
                          >edit</button>
                          <button
                            type="button"
                            className="mem-btn mem-btn-danger"
                            onClick={() => removeMemory(m.id)}
                          >delete</button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-h">Core rules</div>
            <div className="settings-stub">Standing instructions the model always reads.</div>

            <div className="settings-subsection">
              <div className="settings-subsection-h">Persona</div>
              <textarea
                className="persona-textarea"
                value={settings.customSystemPrompt}
                onChange={(e) => setCustomSystemPrompt(e.target.value)}
                placeholder="Nova's system prompt — leave blank to use the default"
                rows={8}
                spellCheck={false}
              />
              <div className="persona-row">
                <div className="settings-stub" style={{ flex: 1 }}>
                  {settings.customSystemPrompt.trim()
                    ? "Custom persona active. Identity + memory blocks still append below this on the backend."
                    : "Using default Nova persona. Blank = default."}
                </div>
                <button
                  type="button"
                  className="persona-reset"
                  onClick={resetCustomSystemPrompt}
                  disabled={!settings.customSystemPrompt}
                  title="Reset persona"
                >Reset to default</button>
              </div>
            </div>
          </section>

          <section className="settings-section">
            <div className="settings-section-h">Permissions</div>
            <div className="settings-stub">
              Tools Nova can use without asking each time.
              <em> read_tile</em> is always-allowed by default (read-only, no side effects).
            </div>
            <NovaPermissionsSection />
          </section>

          <section className="settings-section">
            <div className="settings-section-h">Narrator</div>
            <NarratorRow />
          </section>
          </>}

          {tab === "about" && <>
          <section className="settings-section settings-section-about">
            {/* Deliberately just the identity of the thing. The backend URL and
                the default model used to sit here: both are plumbing, neither
                is actionable, and one of them named our upstream provider. */}
            <div className="about-card">
              <div className="about-app">Infinity</div>
              <div className="about-by">by Anuj Attri</div>
              <div className="about-version">v{typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "0.1.1"}</div>
              <div className="about-copyright">© 2026 Anuj Attri. MIT License.</div>
            </div>
          </section>
          </>}
        </div>
      </aside>
    </>
  );
}


// ── Chat-provider connector row (Anthropic / OpenAI / Gemini) ──────────
// Password-masked input + Save & test. The "test" is the backend's
// validate-then-store flow: POST /chat-providers/{name}/connect calls
// the provider's models endpoint with the key before writing it to disk.
// On 422 we render the error inline and DO NOT save. On success we
// refresh the live model catalog so the chat selector picks up the new
// models immediately. The raw key never round-trips back from the
// backend — once saved, the UI only knows {connected: true}.

type ChatProviderName = "anthropic" | "openai" | "gemini";

function ChatProviderStoreBadge() {
  const { store } = useChatModels();
  if (store.backend === "unknown") return null;
  const ok = store.encrypted;
  return (
    <div
      className="connector-card"
      style={{
        marginTop: 4,
        padding: "6px 10px",
        background: ok ? "rgba(0, 110, 60, 0.06)" : "rgba(180, 60, 0, 0.08)",
        border: ok ? "1px solid rgba(0, 110, 60, 0.2)" : "1px solid rgba(180, 60, 0, 0.25)",
        borderRadius: 6,
        fontSize: 12,
        opacity: 0.85,
      }}
    >
      {ok
        ? "Premium keys are stored encrypted (Windows DPAPI · current-user, current-machine). The file on disk contains only opaque base64 ciphertext."
        : `Encryption backend fell back to "${store.backend}" — keys would be stored AS-IS on disk. Don't paste real premium keys until this is fixed.`}
    </div>
  );
}

function ChatProviderRow({
  name, label, placeholder,
}: { name: ChatProviderName; label: string; placeholder: string }) {
  const { status, store, refresh } = useChatModels();
  const entry = status[name];
  const connected = !!entry?.connected;
  const needsReconnect = !!entry?.needs_reconnect;
  const allowSave = store.encrypted;   // refuse live use on identity fallback

  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onSave = async () => {
    setErr(null);
    if (!allowSave) { setErr("Refusing to save while encryption is unavailable. Restart the backend on Windows."); return; }
    if (!key.trim()) { setErr("Paste an API key first."); return; }
    setBusy(true);
    try {
      await connectChatProvider(name, key.trim());
      setKey("");
      setEditing(false);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDisconnect = async () => {
    setErr(null);
    setBusy(true);
    try {
      await disconnectChatProvider(name);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onPasteFromClipboard = async () => {
    setErr(null);
    try {
      const t = await navigator.clipboard.readText();
      if (t) setKey(t.trim());
      else setErr("Clipboard is empty.");
    } catch (e) {
      // WebView2 sometimes blocks readText without a user gesture path
      // even with permissions set. Caller can still Ctrl+V into the
      // input directly — surface a clear note.
      setErr("Clipboard read blocked — use Ctrl+V into the field instead.");
    }
  };

  return (
    <>
      <div className="settings-row settings-connector-row">
        <span>{label}</span>
        {connected ? (
          <div className="settings-row-actions">
            <span className="pill-status ok">Connected</span>
            <button
              type="button"
              className="settings-action-btn"
              onClick={() => { setErr(null); setEditing((v) => !v); }}
              disabled={busy}
            >{editing ? "Cancel" : "Edit"}</button>
            <button
              type="button"
              className="settings-action-btn settings-action-danger"
              onClick={onDisconnect}
              disabled={busy}
            >Disconnect</button>
          </div>
        ) : needsReconnect ? (
          <div className="settings-row-actions">
            <span className="pill-status off" title="Key decryption failed">
              Reconnect required
            </span>
            <button
              type="button"
              className="settings-action-btn settings-action-primary"
              onClick={() => { setErr(null); setEditing((v) => !v); }}
              disabled={busy}
            >{editing ? "Cancel" : "Reconnect"}</button>
          </div>
        ) : (
          <div className="settings-row-actions">
            <span className="pill-status off">Not connected</span>
            <button
              type="button"
              className="settings-action-btn settings-action-primary"
              onClick={() => { setErr(null); setEditing((v) => !v); }}
              disabled={busy}
            >{editing ? "Cancel" : "Connect"}</button>
          </div>
        )}
      </div>
      {editing && (
        <div className="connector-card">
          <div className="connector-stub">
            Paste your {label} API key. We validate it against {label}'s
            models endpoint before saving. Keys are encrypted at rest via
            Windows DPAPI (bound to your user + machine) and held only in
            process memory while the backend runs — never logged, never
            echoed in any API response.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              onPaste={(e) => {
                // Ensure paste fills the field even if a Tauri-level
                // handler tries to intercept Ctrl+V. The default <input>
                // paste behavior is preserved; this is belt-and-suspenders.
                const text = e.clipboardData?.getData("text");
                if (text) {
                  e.preventDefault();
                  setKey(text.trim());
                }
              }}
              placeholder={placeholder}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              disabled={busy}
              style={{
                flex: 1,
                padding: "6px 10px",
                borderRadius: 6,
                border: "1px solid var(--border, #d8d8da)",
                background: "var(--surface, #fff)",
                color: "inherit",
                fontFamily: "inherit",
                fontSize: 13,
              }}
              onKeyDown={(e) => { if (e.key === "Enter") void onSave(); }}
            />
            <button
              type="button"
              className="settings-action-btn"
              onClick={onPasteFromClipboard}
              disabled={busy}
              title="Paste from clipboard"
            >Paste</button>
            <button
              type="button"
              className="settings-action-btn settings-action-primary"
              onClick={onSave}
              disabled={busy || !key.trim() || !allowSave}
            >{busy ? "Testing…" : "Save & test"}</button>
          </div>
          {err && <div className="connector-error" style={{ marginTop: 6 }}>{err}</div>}
        </div>
      )}
    </>
  );
}




// ── Profile ──────────────────────────────────────────────────────────────────
// The name Nova uses. This was a hardcoded constant in the backend persona
// wrong…"), which meant the app addressed everyone as its author. Now it is
// per-account state, and blank is a valid answer — the persona has a neutral
// form and uses it rather than inventing a name.

function ProfileSection({ open }: { open: boolean }) {
  const [name, setName] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  // Set the moment the user touches the field. The fetch below resolves
  // whenever the network says so, and without this it would overwrite whatever
  // they had already typed — silently, and looking exactly like a field that
  // refuses to accept input. Found by the e2e check for this section.
  const touched = useRef(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getProfile()
      .then((r) => {
        if (!alive) return;
        setSaved(r.display_name || "");
        if (!touched.current) setName(r.display_name || "");
      })
      .catch((e) => { if (alive) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [open]);

  const save = async () => {
    setBusy(true); setErr(null); setDone(false);
    try {
      const r = await setProfile(name.trim());
      setName(r.display_name);
      setSaved(r.display_name);
      setDone(true);
      window.setTimeout(() => setDone(false), 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const dirty = name.trim() !== saved;

  return (
    <>
      <div className="settings-row">
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span>Your name</span>
          <span className="settings-row-meta" style={{ fontSize: 11 }}>
            What Nova calls you. Leave blank and it won't use a name.
          </span>
        </div>
        <div className="settings-row-actions">
          <input
            className="settings-input"
            value={name}
            maxLength={64}
            onChange={(e) => { touched.current = true; setName(e.target.value); }}
            onKeyDown={(e) => { if (e.key === "Enter" && dirty) void save(); }}
            placeholder="your name"
            style={{ width: 150 }}
          />
          <button
            type="button"
            className="settings-action-btn"
            disabled={busy || !dirty}
            onClick={() => void save()}
          >{busy ? "…" : done ? "Saved" : "Save"}</button>
        </div>
      </div>
      {err && <div className="mem-error">{err}</div>}
    </>
  );
}


// ── Narrator toggle ──────────────────────────────────────────────────────────

function NarratorRow() {
  const { enabled, setEnabled } = useNarrator();
  return (
    <div className="settings-row" style={{ marginTop: 8 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span>Background observer</span>
        <span className="settings-row-meta" style={{ fontSize: 11 }}>
          Watches each reply and feeds a silent hint into Nova's next turn.
          Costs extra tokens per turn — opt-in.
        </span>
      </div>
      <button
        type="button"
        className={`seg-btn ${enabled ? "on" : ""}`}
        style={{ minWidth: 52 }}
        onClick={() => setEnabled(!enabled)}
      >
        {enabled ? "on" : "off"}
      </button>
    </div>
  );
}


// ── Devices section (auth) ──────────────────────────────────────────────────
// Lists approved devices, lets the user revoke any, sign out this device, or
// reset the whole auth layer (revoke all + clear password → first-run).

function _relativeTime(epochSec: number | undefined): string {
  if (!epochSec) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epochSec));
  if (s < 45) return "just now";
  if (s < 90) return "a minute ago";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 7200) return "an hour ago";
  if (s < 86400) return `${Math.floor(s / 3600)} hr ago`;
  if (s < 172800) return "yesterday";
  return `${Math.floor(s / 86400)} d ago`;
}

function _shortDate(epochSec: number | undefined): string {
  if (!epochSec) return "—";
  try { return new Date(epochSec * 1000).toLocaleDateString(); } catch { return "—"; }
}

function DevicesSection({ open }: { open: boolean }) {
  const { logout, resetAll } = useAuth();
  const [devices, setDevices] = useState<AuthDevice[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await getAuthDevices();
      setDevices(r.devices);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const revoke = async (d: AuthDevice) => {
    if (!window.confirm(`Revoke "${d.device_name}"? It will be signed out immediately.`)) return;
    try {
      const r = await revokeAuthDevice(d.id);
      // If we just revoked our own device, the 401 handler will bounce us
      // to login on the next call — but make it immediate.
      if (r.self_revoked) { await logout(); return; }
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <div className="settings-stub" style={{ marginBottom: 6 }}>
        Devices signed in to this backend. Tokens are long-lived until revoked.
      </div>

      {err && <div className="mem-error">{err}</div>}
      {loading && devices.length === 0 && <div className="settings-stub">Loading…</div>}
      {!loading && devices.length === 0 && !err && (
        <div className="settings-stub">No devices.</div>
      )}

      <ul className="mem-list">
        {devices.map((d) => (
          <li className="mem-item" key={d.id}>
            <span className="mem-badge mem-badge-profile">device</span>
            <span className="mem-text">
              {d.device_name}
              <span className="settings-row-meta" style={{ marginLeft: 8, fontSize: 11 }}>
                added {_shortDate(d.created_at)} · seen {_relativeTime(d.last_seen_at)}
              </span>
            </span>
            <button
              type="button"
              className="mem-btn mem-btn-danger"
              onClick={() => void revoke(d)}
            >revoke</button>
          </li>
        ))}
      </ul>

      {/* "Provision a device" lived here — it minted a long-lived token to paste
          into an "Eye node", a desktop-only concept that has no counterpart in a
          hosted build. It was also the one control in Settings that could hand
          out a durable credential, which is not something to leave lying around
          in a product about to have real accounts. The /auth/issue route stays
          for now; nothing in the UI calls it. */}

      <div className="settings-row" style={{ marginTop: 10 }}>
        <span>Sign out this device</span>
        <button
          type="button"
          className="settings-action-btn"
          onClick={() => void logout()}
        >Sign out</button>
      </div>

      <div className="settings-subsection" style={{ marginTop: 10 }}>
        <div className="settings-row">
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span>Reset all auth</span>
            <span className="settings-row-meta" style={{ fontSize: 11 }}>
              Revokes every device (including this one) and clears the master password.
            </span>
          </div>
          {confirmReset ? (
            <div className="settings-row-actions">
              <button
                type="button"
                className="settings-action-btn settings-action-danger"
                onClick={() => void resetAll()}
              >Confirm reset</button>
              <button
                type="button"
                className="settings-action-btn"
                onClick={() => setConfirmReset(false)}
              >Cancel</button>
            </div>
          ) : (
            <button
              type="button"
              className="settings-action-btn settings-action-danger"
              onClick={() => setConfirmReset(true)}
            >Reset…</button>
          )}
        </div>
      </div>
    </>
  );
}


// ── Nova permissions section ────────────────────────────────────────────────

const TOOL_LABELS: Record<string, string> = {
  read_tile:        "read_tile — read tile content (always-on default)",
  spawn_tile:       "spawn_tile — open a tile",
  close_tile:       "close_tile — close a tile",
  dispatch_subagent:"dispatch_subagent — run a sub-agent",
};

function NovaPermissionsSection() {
  const { grants, revoke } = useNovaPermissions();

  const alwaysGrants = Object.entries(grants).filter(([, v]) => v === "always");

  if (alwaysGrants.length === 0) {
    return (
      <div className="settings-stub">
        No always-allow grants yet. Click "Allow always" when Nova asks for approval.
      </div>
    );
  }

  return (
    <ul className="mem-list">
      {alwaysGrants.map(([tool]) => (
        <li className="mem-item" key={tool}>
          <span className="mem-badge mem-badge-profile">always</span>
          <span className="mem-text">{TOOL_LABELS[tool] ?? tool}</span>
          {tool !== "read_tile" && (
            <button
              type="button"
              className="mem-btn mem-btn-danger"
              onClick={() => void revoke(tool)}
            >revoke</button>
          )}
        </li>
      ))}
    </ul>
  );
}
