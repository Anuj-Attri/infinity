// Thin HTTP wrapper around the FastAPI backend.
//
// Mark 3 resolved this four ways — packaged Tauri, dev Tauri, Capacitor, and
// browser — because it shipped as a desktop app, an Android app, and a page,
// all pointed at one machine on a Tailscale network. Mark 4 is a web app
// talking to a hosted backend, so there is exactly one question: which origin?
//
//   VITE_API_BASE_URL   explicit, and what production always sets.
//   (unset)             http://127.0.0.1:8000 — local dev against a local API.
//
// The old fallback was a hardcoded Tailscale IP over plain http. That is gone
// for three reasons: the tailnet is not part of this architecture any more; a
// baked-in IP is not deployable; and a plain-http origin has no secure context,
// which silently removes navigator.clipboard, getUserMedia and SpeechRecognition.
const DEV_FALLBACK_URL = "http://127.0.0.1:8000";

function resolveBackendBase(): string {
  const configured = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
  if (configured) return configured.replace(/\/+$/, "");

  // No explicit URL in a production build means the backend is serving this page
  // itself — the container builds the SPA into its own image and mounts dist/ at
  // "/". Same origin, so there is no CORS surface at all and nothing to
  // misconfigure.
  //
  // If the frontend is ever hosted separately (Cloudflare Pages), that deploy
  // MUST set VITE_API_BASE_URL, or every call goes to the static host and 404s.
  if (import.meta.env.PROD) return window.location.origin;

  return DEV_FALLBACK_URL;
}

const BASE = resolveBackendBase();

/** The backend origin this client talks to. */
export function getBackendBase(): string {
  return BASE;
}

// ── Auth plumbing ────────────────────────────────────────────────────
// Every backend call (except the auth bootstrap endpoints) goes through
// apiFetch, which attaches the device's Bearer token and watches for a
// 401. A 401 means the token was revoked or is otherwise invalid — we
// notify the registered handler so AuthContext can clear it and bounce
// the user back to the login screen.

let _authToken: string | null = null;
let _onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null): void {
  _authToken = token;
}
/** Raw bearer, for callers that must fetch outside apiFetch. */
export function getAuthToken(): string | null {
  return _authToken;
}
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  _onUnauthorized = fn;
}

async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  if (_authToken) headers.set("Authorization", `Bearer ${_authToken}`);
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) _onUnauthorized?.();
  return res;
}

// ── Auth bootstrap endpoints (use raw fetch — must work with no token,
// and a 401 here is an expected login failure, not a session expiry) ──

export type AuthStatus = {
  password_set: boolean;
  store_backend: string;
  /** True when the deployment requires an out-of-band SETUP_TOKEN to claim it. */
  setup_token_required?: boolean;
  /** False when first-run setup is disabled entirely for this caller. */
  setup_available?: boolean;
};
export type AuthDevice = {
  id: string;
  device_name: string;
  created_at: number;
  last_seen_at: number;
  token_preview: string;
};

export async function getAuthStatus(): Promise<AuthStatus> {
  const r = await apiFetch(`${BASE}/auth/status`);
  if (!r.ok) throw new Error(`auth/status ${r.status}`);
  return r.json();
}

export async function authSetup(password: string, setupToken?: string): Promise<void> {
  const r = await apiFetch(`${BASE}/auth/setup`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(
      setupToken ? { password, setup_token: setupToken } : { password },
    ),
  });
  if (!r.ok) {
    let detail = "Setup failed";
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
}

export async function authLogin(password: string, deviceName: string): Promise<{ token: string; device_name: string }> {
  // Raw fetch: a 401 here is an expected bad-password, not a session expiry —
  // must NOT trip the global unauthorized handler.
  const r = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password, device_name: deviceName }),
  });
  if (!r.ok) {
    // Always generic — the backend never tells us why.
    throw new Error("Authentication failed");
  }
  return r.json();
}

export type AuthMe = { device_name: string; created_at: number; last_seen_at: number };

export async function getAuthMe(): Promise<AuthMe | null> {
  // Uses apiFetch so a revoked token clears state via the 401 handler.
  const r = await apiFetch(`${BASE}/auth/me`);
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`auth/me ${r.status}`);
  return r.json();
}

export async function getAuthDevices(): Promise<{ devices: AuthDevice[] }> {
  const r = await apiFetch(`${BASE}/auth/devices`);
  if (!r.ok) throw new Error(`auth/devices ${r.status}`);
  return r.json();
}

/** Provision a token for another device (e.g. the Eye node). The full token is
 * returned ONCE — copy it into that device's config. */
export async function provisionDevice(device_name: string): Promise<{ token: string; device_name: string }> {
  const r = await apiFetch(`${BASE}/auth/issue`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ device_name }),
  });
  if (!r.ok) throw new Error(`auth/issue ${r.status}`);
  return r.json();
}

export async function revokeAuthDevice(id: string): Promise<{ ok: boolean; self_revoked: boolean }> {
  const r = await apiFetch(`${BASE}/auth/devices/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`revoke device ${r.status}`);
  return r.json();
}

export async function authLogout(): Promise<void> {
  await apiFetch(`${BASE}/auth/logout`, { method: "POST" });
}

export async function authReset(): Promise<void> {
  const r = await apiFetch(`${BASE}/auth/reset`, { method: "POST" });
  if (!r.ok) throw new Error(`auth/reset ${r.status}`);
}

export type Headline = {
  title: string;
  source: string;
  url: string;
  published_at: string | null;
};

export async function getNews(
  q?: string,
): Promise<{ headlines: Headline[]; fetched_at: string; cached: boolean }> {
  const query = (q ?? "").trim();
  const qs = query ? `?q=${encodeURIComponent(query)}` : "";
  const r = await apiFetch(`${BASE}/news${qs}`);
  if (!r.ok) {
    let detail = `news ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// One niche game/anime quote from the LOCAL model. The Quote tile owns the
// once-a-day scheduling + caching; this is just the generate call.
export async function getQuote(): Promise<{ quote: string }> {
  const r = await apiFetch(`${BASE}/quote`, { method: "POST" });
  if (!r.ok) {
    let detail = `quote ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// ── RAG (document Q&A) ───────────────────────────────────────────
// Ingest reuses the local nomic-embed stack (server-side); the key never
// leaves the backend. Doc vectors live in a separate SQLite table.

export type DocChunk = { source: string; chunk_index: number; text: string; score: number };

// Grounded populate-on-index preview: a source's leading chunks (verbatim) +
// the full indexed-doc set. Local-only, no model. Used by the Docs tile.
export type RagPreview = {
  source: string;
  chunks: number;
  excerpt: string;
  docs: Array<{ source: string; chunks: number }>;
};

export async function ragPreview(source: string, n = 2): Promise<RagPreview> {
  const r = await apiFetch(`${BASE}/rag/preview?source=${encodeURIComponent(source)}&n=${n}`);
  if (!r.ok) throw new Error(`rag/preview ${r.status}`);
  return r.json();
}

// Vision graph (the /vision tile). One node per indexed chunk/memory; links
// are precomputed top-K semantic-similarity edges. Read-only; fails closed to an
// empty graph so the tile can render an empty state without crashing.
export type RagGraphNode = {
  id: string;
  label: string;
  source: string;
  type: "doc" | "memory" | string;
  memtype?: string;
  text: string;
};
export type RagGraphLink = { source: string; target: string; value: number };
export type RagGraph = { nodes: RagGraphNode[]; links: RagGraphLink[] };

export async function getRagGraph(): Promise<RagGraph> {
  try {
    const r = await apiFetch(`${BASE}/rag/graph`);
    if (!r.ok) return { nodes: [], links: [] };
    return r.json();
  } catch {
    return { nodes: [], links: [] };
  }
}

// The currently-indexed documents in the RAG store (incl. prior sessions),
// newest first. Used by the Docs tile to reflect indexed docs on spawn.
export async function getRagDocs(): Promise<{ docs: Array<{ source: string; doc_id: string; chunks: number }> }> {
  try {
    const r = await apiFetch(`${BASE}/rag/docs`);
    if (!r.ok) return { docs: [] };
    return r.json();
  } catch {
    return { docs: [] };
  }
}

// Clear ALL indexed documents (every file/link/quick-add) in one action.
// Wipes the doc_chunks table ONLY — Nova's memories + session_overview are
// untouched (separate namespaces). Destructive + irreversible. Returns the
// chunk count removed. Routed via apiFetch (Bearer + resolved backend base) so
// it works over the tailnet from phone/laptop too, like every other RAG call.
export async function clearRagDocs(): Promise<{ ok: boolean; removed: number }> {
  const r = await apiFetch(`${BASE}/rag/docs`, { method: "DELETE" });
  if (!r.ok) throw new Error(`rag/docs clear ${r.status}`);
  return r.json();
}

export async function ragIngest(name: string, text: string): Promise<{
  ok: boolean; already: boolean; doc_id: string; chunks: number; source: string;
}> {
  const r = await apiFetch(`${BASE}/rag/ingest`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, text }),
  });
  if (!r.ok) {
    let detail = `ingest ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// ── Code tile filesystem (/fs) ────────────────────────────────────────────
// Go through apiFetch like every other endpoint, so the device Bearer token is
// attached (the /fs routes are behind require_auth) AND the correct backend base
// is used (resolveBackendBase — loopback on desktop, the home backend over the
// tailnet on the packaged/phone builds). The Code tile previously used a raw
// fetch() to a hardcoded 127.0.0.1, so once the auth gate landed every /fs call
// 401'd → "Failed to fetch" in the WebView.
export async function fsTree(path: string): Promise<{ tree: unknown }> {
  const r = await apiFetch(`${BASE}/fs/tree?path=${encodeURIComponent(path)}`);
  if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `tree ${r.status}`); }
  return r.json();
}
export async function fsRead(path: string): Promise<{ path: string; content: string }> {
  const r = await apiFetch(`${BASE}/fs/read?path=${encodeURIComponent(path)}`);
  if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `read ${r.status}`); }
  return r.json();
}
export async function fsWrite(path: string, content: string): Promise<void> {
  const r = await apiFetch(`${BASE}/fs/write`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.detail || `write ${r.status}`); }
}

// Quick-add a LINK to the shared RAG index: the backend fetches + extracts the
// page server-side (no CORS) and ingests it, so a URL becomes searchable
// knowledge + a brain node. Fails closed (throws on fetch/extract failure).
export async function ragIngestUrl(url: string): Promise<{
  ok: boolean; already: boolean; doc_id: string; chunks: number; source: string;
}> {
  const r = await apiFetch(`${BASE}/rag/ingest_url`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) {
    let detail = `ingest ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

// Authed document extraction (PDF/docx/xlsx/pptx -> text + rich view). Used at
// upload time. MUST go through apiFetch — /files/extract is behind require_auth,
// so a token-less fetch 401s and the doc silently degrades to binary.
export async function extractFile(
  file: File,
): Promise<{ text: string; view?: unknown } | null> {
  const fd = new FormData();
  fd.append("file", file, file.name);
  try {
    const r = await apiFetch(`${BASE}/files/extract`, { method: "POST", body: fd });
    if (!r.ok) return null;
    const b = await r.json();
    if (b && b.kind === "text" && typeof b.text === "string" && b.text.length) {
      return { text: b.text, view: b.view ?? undefined };
    }
    return null;
  } catch { return null; }
}

// Ingest a document for RAG from its ACTUAL bytes — the backend runs the real
// extractor and fails loudly (422 "No readable text.") if nothing is recovered.
export async function ragIngestFile(file: Blob, name: string): Promise<{
  ok: boolean; already: boolean; doc_id: string; chunks: number; source: string;
}> {
  const fd = new FormData();
  fd.append("file", file, name);
  const r = await apiFetch(`${BASE}/rag/ingest_file`, { method: "POST", body: fd });
  if (!r.ok) {
    let detail = `ingest ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// OpenRouter multimodal content part. The frontend gather-by-kind assembler
// (contentParts.ts) emits these; the backend wraps them in the user message and
// the omni-model (gemini-3.1-flash-lite) processes them jointly. Adding a
// modality = one new variant + one assembler case, not a rewrite.
export type ContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } }
  | { type: "input_audio"; input_audio: { data: string; format: string } }
  | { type: "file"; file: { filename: string; file_data: string } };

// Summary of frontend-gathered tile content. Source gathering stays LOCAL
// (tile-read + RAG + raw bytes); only this SYNTHESIS step is DISPATCHED to
// OpenRouter (Gemini 3.1 Flash-Lite — multimodal in) and streamed back as
// NDJSON. Now CONTENT-AWARE: image/audio sources are sent as real bytes
// (content parts), not text metadata. Errors are opaque ("Summary
// unavailable.") — the backend never leaks a billing/credit reason.
export type SummarizeCallbacks = {
  onDelta: (chunk: string) => void;
  onDone?: (model: string) => void;
  onError?: (msg: string) => void;
};

export async function postSummarize(
  parts: ContentPart[],
  focus: string | undefined,
  { onDelta, onDone, onError }: SummarizeCallbacks,
): Promise<void> {
  let r: Response;
  try {
    r = await apiFetch(`${BASE}/summarize`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ parts, focus: focus || undefined }),
    });
  } catch {
    onError?.("Summary unavailable.");
    return;
  }
  if (!r.ok || !r.body) {
    onError?.("Summary unavailable.");
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) {
        try {
          const obj = JSON.parse(line);
          if (typeof obj.delta === "string") onDelta(obj.delta);
          else if (obj.error) onError?.("Summary unavailable.");
          else if (obj.done) onDone?.(String(obj.model || ""));
        } catch { /* ignore non-JSON keepalive lines */ }
      }
      nl = buf.indexOf("\n");
    }
  }
}

// Generate (or edit) an image via the OpenRouter dispatch lane (Nano Banana).
// Source gathering (the optional edit image) stays client-side; only synthesis
// dispatches. Errors are opaque ("<Kind> unavailable.") — no leaked reason.
// `source` is a data URL (image-to-image); omit for text-to-X.
// Unified across the whole generative suite (/image, /audio).
export async function postGenerate(
  kind: "image" | "audio",
  prompt: string,
  source: string | undefined,
  opaque: string,
): Promise<{ kind: string; data: string; text: string }> {
  let r: Response;
  try {
    r = await apiFetch(`${BASE}/generate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind, prompt, image: source || undefined }),
    });
  } catch {
    throw new Error(opaque);
  }
  if (!r.ok) throw new Error(opaque);
  const b = await r.json();
  if (!b || typeof b.data !== "string" || !b.data) throw new Error(opaque);
  return { kind: String(b.kind || kind), data: b.data, text: typeof b.text === "string" ? b.text : "" };
}

// Balanced source-check via the dispatch lane (Sonar web search → Flash-Lite
// synthesis). Streams NDJSON: {stage} events drive the progress bar, {delta}
// streams the brief. `parts` (a referenced source) skips the web step. Errors
// are opaque ("Research unavailable.") — no leaked reason.
export type ResearchCallbacks = {
  onStage?: (stage: string) => void;
  onDelta: (chunk: string) => void;
  onDone?: () => void;
  onError?: () => void;
};

export async function postResearch(
  prompt: string,
  parts: ContentPart[],
  focus: string | undefined,
  { onStage, onDelta, onDone, onError }: ResearchCallbacks,
): Promise<void> {
  let r: Response;
  try {
    r = await apiFetch(`${BASE}/research`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ prompt, parts, focus: focus || undefined }),
    });
  } catch { onError?.(); return; }
  if (!r.ok || !r.body) { onError?.(); return; }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) {
        try {
          const obj = JSON.parse(line);
          if (typeof obj.delta === "string") onDelta(obj.delta);
          else if (typeof obj.stage === "string") onStage?.(obj.stage);
          else if (obj.error) onError?.();
          else if (obj.done) onDone?.();
        } catch { /* ignore keepalives */ }
      }
      nl = buf.indexOf("\n");
    }
  }
}

// Milestone-structured plan synthesis (final step of the /plan recipe). The
// frontend gathers research (via /research) + an optional diagram (via /image),
// then sends goal + findings here; Flash-Lite streams a dense, milestone plan
// (NO time estimates). Opaque "Plan unavailable." on failure.
export async function postPlan(
  goal: string,
  findings: string,
  diagram: boolean,
  { onDelta, onDone, onError }: SummarizeCallbacks,
): Promise<void> {
  let r: Response;
  try {
    r = await apiFetch(`${BASE}/plan`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ goal, findings, diagram }),
    });
  } catch { onError?.("Plan unavailable."); return; }
  if (!r.ok || !r.body) { onError?.("Plan unavailable."); return; }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) {
        try {
          const obj = JSON.parse(line);
          if (typeof obj.delta === "string") onDelta(obj.delta);
          else if (obj.error) onError?.("Plan unavailable.");
          else if (obj.done) onDone?.("");
        } catch { /* ignore keepalives */ }
      }
      nl = buf.indexOf("\n");
    }
  }
}

// ── Session overview (ephemeral grounding index) ─────────────────────
// POST the gathered live tile data; backend chunks+embeds it into the
// single-slot session-overview namespace (overwrite). Chat retrieves from it
// each turn for grounding. Fail-closed → 0 chunks, chat just runs ungrounded.
export async function postSessionOverview(text: string): Promise<{ chunks: number }> {
  try {
    const r = await apiFetch(`${BASE}/session/overview`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) return { chunks: 0 };
    return r.json();
  } catch {
    return { chunks: 0 };
  }
}

// ── Self-hosted update (approve-to-install) ──────────────────────────
// The home backend serves the latest .msi. Check is best-effort: unreachable /
// no update → null (the client shows no prompt, no error spam).
export async function getLatestUpdate(): Promise<{ version: string | null; notes: string }> {
  try {
    const r = await apiFetch(`${BASE}/update/latest`);
    if (!r.ok) return { version: null, notes: "" };
    const b = await r.json();
    return { version: typeof b?.version === "string" ? b.version : null, notes: String(b?.notes || "") };
  } catch {
    return { version: null, notes: "" };
  }
}

/** Download the latest .msi as a Blob (authed). Throws on failure. */
export async function downloadUpdate(): Promise<Blob> {
  const r = await apiFetch(`${BASE}/update/download`);
  if (!r.ok || !r.body) throw new Error("Update download failed.");
  return r.blob();
}

// ── /animate — local Hyperframes motion-graphics render (streamed) ─────────
export type AnimateCallbacks = {
  onStage?: (stage: string, frames?: number, total?: number) => void;
  onDone: (url: string) => void;
  onError?: () => void;
};
export async function postAnimate(prompt: string, { onStage, onDone, onError }: AnimateCallbacks, notifyRoute?: string): Promise<void> {
  try {
    const r = await apiFetch(`${BASE}/animate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      // notify_route: where the "render ready" notification lands when tapped.
      // The backend enqueues it at render-done so it survives backgrounding.
      body: JSON.stringify({ prompt, notify_route: notifyRoute }),
    });
    if (!r.ok || !r.body) { onError?.(); return; }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let finished = false;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const obj = JSON.parse(line);
          if (typeof obj.stage === "string") onStage?.(obj.stage, obj.frames, obj.total);
          else if (obj.done && typeof obj.url === "string") { finished = true; onDone(obj.url); }
          else if (obj.error) { finished = true; onError?.(); }
        } catch { /* partial line */ }
      }
    }
    if (!finished) onError?.();
  } catch {
    onError?.();
  }
}

// ── Notifications (earned-and-rare bus) ──────────────────────────────
export type NotifyItem = {
  id: number;
  ts: number;
  category: string;         // job | approval | failed | nudge | connection
  title: string;
  body: string;
  route: string | null;     // "tile:<id>" | "animate:<file>" | …
  data?: Record<string, unknown>;
};
export type NotifySettings = {
  enabled: boolean;
  briefing_enabled: boolean;
  quiet_start: number;
  quiet_end: number;
  min_confidence: number;
  rate_cap: number;
  rate_window_h: number;
};

/** Drain notifications the phone hasn't seen (id > since). Already gated. */
export async function pollNotifications(since: number): Promise<{ items: NotifyItem[]; last_id: number }> {
  const r = await apiFetch(`${BASE}/notify/poll?since=${since}`);
  if (!r.ok) throw new Error(`notify/poll ${r.status}`);
  return r.json();
}
export async function getNotifySettings(): Promise<NotifySettings> {
  const r = await apiFetch(`${BASE}/notify/settings`);
  if (!r.ok) throw new Error(`notify/settings ${r.status}`);
  return r.json();
}
export async function setNotifySettings(patch: Partial<NotifySettings>): Promise<NotifySettings> {
  const r = await apiFetch(`${BASE}/notify/settings`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`notify/settings ${r.status}`);
  return r.json();
}

/** Fetch a rendered animation as a Blob (authed) — for an object-URL <video>. */
export async function fetchAnimateBlob(path: string): Promise<Blob> {
  const r = await apiFetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`animation fetch ${r.status}`);
  return r.blob();
}

/** Mint a short-lived ticketed URL for a rendered animation and return it as
 *  an ABSOLUTE src for a <video> tag. Media elements can't send Authorization
 *  headers, and Android WebView's player wants a plain streamable http URL
 *  (with Range) — blob object-URLs are the flaky path there. The mint call
 *  itself is authed; the returned URL admits exactly that one file for a few
 *  minutes. */
export async function fetchAnimatePlayableUrl(path: string): Promise<string> {
  const r = await apiFetch(`${BASE}/animate/ticket`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url: path }),
  });
  if (!r.ok) throw new Error(`animation ticket ${r.status}`);
  const j = (await r.json()) as { url?: string };
  if (!j.url) throw new Error("animation ticket: no url");
  return `${BASE}${j.url}`;
}

// Android self-update: manifest + APK from the home backend (tailnet, authed).
export type AndroidUpdate = { versionCode: number | null; versionName?: string; file?: string; notes?: string };
export async function getAndroidUpdate(): Promise<AndroidUpdate> {
  try {
    const r = await apiFetch(`${BASE}/update/android/latest.json`);
    if (!r.ok) return { versionCode: null };
    return await r.json();
  } catch {
    return { versionCode: null };
  }
}
export async function downloadAndroidApk(file: string): Promise<Blob> {
  const r = await apiFetch(`${BASE}/update/android/${encodeURIComponent(file)}`);
  if (!r.ok) throw new Error(`APK download failed (${r.status}).`);
  return r.blob();
}

export async function ragQuery(question: string, k?: number): Promise<{ chunks: DocChunk[] }> {
  const r = await apiFetch(`${BASE}/rag/query`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, k }),
  });
  if (!r.ok) {
    let detail = `query ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export type KeyEntry = {
  name: string;
  label: string;
  group: "ai" | "data";
  present: boolean;
  masked: string | null;
};

export async function getKeyStatus(): Promise<{ keys: KeyEntry[] }> {
  const r = await apiFetch(`${BASE}/keys/status`);
  if (!r.ok) throw new Error(`keys/status ${r.status}`);
  return r.json();
}

export type Quote = {
  symbol: string;
  price: number | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  currency: string | null;
  error: string | null;
};

export async function getStocks(symbols: string[]): Promise<{ quotes: Quote[]; fetched_at: string; cached_count: number }> {
  const r = await apiFetch(`${BASE}/stocks?symbols=${encodeURIComponent(symbols.join(","))}`);
  if (!r.ok) {
    let detail = `stocks ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export type Mover = {
  symbol: string;
  name: string | null;
  price: number | null;
  change_pct: number | null;
};

export async function getStockMovers(): Promise<{
  gainers: Mover[]; losers: Mover[]; fetched_at: string; cached: boolean;
}> {
  const r = await apiFetch(`${BASE}/stocks/movers`);
  if (!r.ok) {
    let detail = `movers ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export type HistoryPoint = { ts: string; price: number };
export type StockRange = "1m" | "5m" | "15m" | "1h" | "1D" | "1W" | "1M" | "3M" | "1Y";

export async function getStockHistory(symbol: string, range: StockRange = "1D"): Promise<{ symbol: string; range: string; points: HistoryPoint[]; cached: boolean }> {
  const r = await apiFetch(`${BASE}/stocks/history?symbol=${encodeURIComponent(symbol)}&range=${range}`);
  if (!r.ok) {
    let detail = `history ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export type SearchResult = {
  symbol: string;
  name: string;
  exchange: string | null;
  type: string | null;
};

export async function searchStocks(query: string, signal?: AbortSignal): Promise<{ query: string; results: SearchResult[] }> {
  const r = await apiFetch(`${BASE}/stocks/search?q=${encodeURIComponent(query)}`, { signal });
  if (!r.ok) {
    let detail = `search ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// ── Memory ──────────────────────────────────────────────────────────

export type MemoryItem = {
  id: string;
  type: "profile" | "episodic";
  text: string;
  source: string;
  confidence: number;
  entities: string[];
  created_at: number;
  updated_at: number;
  score?: number;
};

export async function listMemories(): Promise<{ items: MemoryItem[] }> {
  const r = await apiFetch(`${BASE}/memory`);
  if (!r.ok) throw new Error(`list memories ${r.status}`);
  return r.json();
}

export async function createMemory(input: { type: "profile" | "episodic"; text: string; confidence?: number; entities?: string[] }): Promise<{ item: MemoryItem }> {
  const r = await apiFetch(`${BASE}/memory`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!r.ok) {
    let detail = `create memory ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function updateMemory(id: string, patch: Partial<{ type: "profile" | "episodic"; text: string; confidence: number; entities: string[] }>): Promise<{ item: MemoryItem }> {
  const r = await apiFetch(`${BASE}/memory/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    let detail = `update memory ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function deleteMemory(id: string): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${BASE}/memory/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`delete memory ${r.status}`);
  return r.json();
}

export async function getStockSummary(quotes: Quote[]): Promise<{ line: string; model: string; cached: boolean }> {
  const r = await apiFetch(`${BASE}/stocks/summary`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ quotes }),
  });
  if (!r.ok) {
    let detail = `summary ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// ── Vision: open-vocab live detection (YOLO-World on REACTOR's GPU) ──
export type VisionBox = { label: string; confidence: number; box: [number, number, number, number] };
export type VisionResult = { detections: VisionBox[]; prompt: string[]; ms: number; device: string };

/** POST one JPEG frame + open-vocab prompt → normalized detections. */
export async function visionDetect(
  jpeg: Blob, prompt: string, opts?: { imgsz?: number; conf?: number },
): Promise<VisionResult> {
  const fd = new FormData();
  fd.append("frame", jpeg, "frame.jpg");
  fd.append("prompt", prompt);
  if (opts?.imgsz) fd.append("imgsz", String(opts.imgsz));
  if (opts?.conf != null) fd.append("conf", String(opts.conf));
  const r = await apiFetch(`${BASE}/vision/detect`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`vision ${r.status}`);
  return r.json();
}

export async function visionStatus(): Promise<{
  ready: boolean; model: string; device?: string;
  vram?: { free_mb: number; total_mb: number } | null;
}> {
  const r = await apiFetch(`${BASE}/vision/status`);
  if (!r.ok) throw new Error(`vision status ${r.status}`);
  return r.json();
}

// ── The Eye of Infinity (the XIAO remote node) ──
// The node POSTs frames/audio to the backend; the tile only READS the cached
// state and sets the mode. Fail-closed: `online:false` => no frame, no boxes.
export type EyePulse = { bpm: number | null; quality: string; note: string };
export type EyeState = {
  online: boolean;
  mode: string;
  source: string;
  age_ms: number | null;
  stale_s: number;
  message: string;
  frame: string | null;             // data URL while online, null when offline
  frame_ms?: number | null;
  detections: VisionBox[];
  infer_ms?: number;
  device?: string;
  prompt?: string;
  text: string;                     // scene/read result
  pulse: EyePulse;
  transcript?: string;
  transcript_age_ms?: number | null;
};
export type EyeConfig = { modes: string[]; mode: string; prompt: string; stale_s: number; vlm_model: string };

export async function getEyeState(): Promise<EyeState> {
  const r = await apiFetch(`${BASE}/vision/eye/state`);
  if (!r.ok) throw new Error(`eye state ${r.status}`);
  return r.json();
}
export async function getEyeConfig(): Promise<EyeConfig> {
  const r = await apiFetch(`${BASE}/vision/eye/config`);
  if (!r.ok) throw new Error(`eye config ${r.status}`);
  return r.json();
}
export async function setEyeMode(
  mode: string, prompt?: string,
): Promise<{ ok: boolean; mode: string; prompt: string }> {
  const r = await apiFetch(`${BASE}/vision/eye/mode`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(prompt != null ? { mode, prompt } : { mode }),
  });
  if (!r.ok) throw new Error(`eye mode ${r.status}`);
  return r.json();
}

// ── MCP connectors (connect any Streamable-HTTP MCP server) ──
export type McpServer = { id: string; name: string; url: string; has_auth: boolean; trusted: boolean; insecure: boolean; tool_count: number; exposed: number; connected_at?: number };
export type McpTool = { server_id?: string; server_name?: string; name: string; description: string; input_schema: unknown };
export type McpMarketItem = { name: string; category: string; desc: string; url_hint: string; needs_auth: string };
export type McpAuditEvent = { ts: number; server_id: string; server: string; tool: string; args: unknown; decision: string };

export async function getMcpMarketplace(): Promise<{ items: McpMarketItem[] }> {
  const r = await apiFetch(`${BASE}/mcp/marketplace`); if (!r.ok) throw new Error(`mcp marketplace ${r.status}`); return r.json();
}
export async function getMcpServers(): Promise<{ servers: McpServer[]; enabled: boolean }> {
  const r = await apiFetch(`${BASE}/mcp/servers`); if (!r.ok) throw new Error(`mcp servers ${r.status}`); return r.json();
}
export async function trustMcp(id: string, trusted: boolean): Promise<{ ok: boolean; trusted: boolean }> {
  const r = await apiFetch(`${BASE}/mcp/trust/${encodeURIComponent(id)}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ trusted }) });
  if (!r.ok) throw new Error(`mcp trust ${r.status}`); return r.json();
}
export async function setMcpEnabled(enabled: boolean): Promise<{ ok: boolean; enabled: boolean }> {
  const r = await apiFetch(`${BASE}/mcp/enabled`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled }) });
  if (!r.ok) throw new Error(`mcp enabled ${r.status}`); return r.json();
}
export async function disconnectAllMcp(): Promise<{ ok: boolean; removed: number }> {
  const r = await apiFetch(`${BASE}/mcp/disconnect-all`, { method: "POST" }); if (!r.ok) throw new Error(`mcp off ${r.status}`); return r.json();
}
export async function getMcpAudit(): Promise<{ events: McpAuditEvent[] }> {
  const r = await apiFetch(`${BASE}/mcp/audit`); if (!r.ok) throw new Error(`mcp audit ${r.status}`); return r.json();
}
export async function mcpAuditDeny(serverId: string, name: string, args: Record<string, unknown>): Promise<void> {
  try { await apiFetch(`${BASE}/mcp/audit/deny`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ server_id: serverId, name, args }) }); } catch { /* best-effort */ }
}
export async function getMcpTools(): Promise<{ tools: McpTool[] }> {
  const r = await apiFetch(`${BASE}/mcp/tools`); if (!r.ok) throw new Error(`mcp tools ${r.status}`); return r.json();
}
export async function connectMcp(name: string, url: string, auth?: string): Promise<{ ok: boolean; id: string; name: string; trusted: boolean; insecure: boolean; tools: McpTool[] }> {
  const r = await apiFetch(`${BASE}/mcp/connect`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name, url, auth: auth || null }) });
  if (!r.ok) { let d = `connect ${r.status}`; try { const b = await r.json(); if (b?.detail) d = String(b.detail); } catch {} throw new Error(d); }
  return r.json();
}
export async function disconnectMcp(id: string): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${BASE}/mcp/servers/${encodeURIComponent(id)}`, { method: "DELETE" }); if (!r.ok) throw new Error(`mcp disconnect ${r.status}`); return r.json();
}
// Consent gate: gated tools return {needs_approval:true} until re-called with
// approved=true (which the frontend only sets after the user clicked Allow).
export async function mcpCall(serverId: string, name: string, args: Record<string, unknown>, approved = false): Promise<{ ok: boolean; text?: string; is_error?: boolean; needs_approval?: boolean }> {
  const r = await apiFetch(`${BASE}/mcp/call`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ server_id: serverId, name, args, approved }) });
  if (!r.ok) { let d = `mcp call ${r.status}`; try { const b = await r.json(); if (b?.detail) d = String(b.detail); } catch {} throw new Error(d); }
  return r.json();
}

export async function getHealth(): Promise<{ status: string; service: string }> {
  const r = await apiFetch(`${BASE}/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function postEcho(message: string): Promise<{ reply: string }> {
  const r = await apiFetch(`${BASE}/echo`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!r.ok) throw new Error(`echo ${r.status}`);
  return r.json();
}

// Non-streaming fallback. Kept so callers that don't want to manage a stream
// (or hit a non-Ollama provider once we add them) still work.
export async function postChat(
  message: string,
): Promise<{ reply: string; model: string }> {
  const r = await apiFetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!r.ok) {
    let detail = `chat ${r.status}`;
    try {
      const body = await r.json();
      if (body?.detail) detail = String(body.detail);
    } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export type HistoryTurn = { role: "user" | "agent"; text: string };

export type MemoryUsed = { id: string; type: string; score: number; text: string };

export type StreamCallbacks = {
  onDelta: (chunk: string) => void;
  /** `truncated` = the reply hit a length/context stop (never silent). */
  onDone?: (model: string, memoryUsed?: MemoryUsed[], truncated?: boolean) => void;
  onError?: (msg: string) => void;
  signal?: AbortSignal;
};

export type ChatOpts = {
  model?: string;
  noThink?: boolean;
  history?: HistoryTurn[];
  memoryEnabled?: boolean;
  /** User-supplied persona override from Settings. Empty/undefined => backend
   *  uses DEFAULT_PERSONA. Identity/memory blocks still append after either. */
  customSystemPrompt?: string;
  /** Injected silently from the Narrator observer. Never shown in transcript. */
  narratorNote?: string | null;
  /** Tags this turn's spend to a Mission Control run. */
  missionId?: string | null;
  /** Spoken turn: backend adds the voice-mode contract + hard brevity clamp. */
  voiceMode?: boolean;
};

// Streams the reply. Reads NDJSON line by line; each line is one of:
//   {"delta": "..."}
//   {"done": true, "model": "..."}
//   {"error": "..."}
export async function postChatStream(
  message: string,
  { onDelta, onDone, onError, signal }: StreamCallbacks,
  opts: ChatOpts = {},
): Promise<void> {
  const payload: Record<string, unknown> = { message };
  if (opts.model) payload.model = opts.model;
  if (opts.noThink !== undefined) payload.no_think = opts.noThink;
  if (opts.history) payload.history = opts.history;
  if (opts.memoryEnabled !== undefined) payload.memory_enabled = opts.memoryEnabled;
  if (opts.customSystemPrompt && opts.customSystemPrompt.trim()) {
    payload.custom_system_prompt = opts.customSystemPrompt;
  }
  if (opts.narratorNote) {
    payload.narrator_note = opts.narratorNote;
  }
  if (opts.missionId) {
    payload.mission_id = opts.missionId;
  }
  if (opts.voiceMode) {
    payload.voice_mode = true;
  }

  let r: Response;
  try {
    r = await apiFetch(`${BASE}/chat/stream`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (e) {
    onError?.(e instanceof Error ? e.message : String(e));
    return;
  }

  if (!r.ok || !r.body) {
    let detail = `chat/stream ${r.status}`;
    try {
      const body = await r.json();
      if (body?.detail) detail = String(body.detail);
    } catch {}
    onError?.(detail);
    return;
  }

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) {
        try {
          const obj = JSON.parse(line);
          if (typeof obj.delta === "string") onDelta(obj.delta);
          else if (obj.error) onError?.(String(obj.error));
          else if (obj.done) onDone?.(String(obj.model || ""), obj.memory_used, !!obj.truncated);
        } catch {
          // skip malformed line
        }
      }
      nl = buf.indexOf("\n");
    }
  }
}


// ── Browsing (server-side reader) ────────────────────────────────
// The backend fetches, extracts and sanitizes; the tile renders the result.
// No child webview, no iframe — this is the only browsing path that can
// survive being hosted, and it is SSRF-hardened on the server.

export type BrowsePage = {
  url: string;
  final_url: string;
  title: string;
  site: string;
  text: string;
  html: string;
  links: { href: string; label: string }[];
  truncated: boolean;
};

export async function browseFetch(url: string): Promise<BrowsePage> {
  const r = await apiFetch(`${BASE}/browse/fetch?url=${encodeURIComponent(url)}`);
  if (!r.ok) {
    let detail = `browse/fetch ${r.status}`;
    try { detail = (await r.json())?.detail || detail; } catch { /* keep default */ }
    throw new Error(detail);
  }
  return r.json();
}

export async function browseIngest(url: string): Promise<{ ok: boolean; name: string; url: string; chars: number }> {
  const r = await apiFetch(`${BASE}/browse/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) {
    let detail = `browse/ingest ${r.status}`;
    try { detail = (await r.json())?.detail || detail; } catch { /* keep default */ }
    throw new Error(detail);
  }
  return r.json();
}

// ── Chat providers (BYOK premium keys) ───────────────────────────
// User-facing chat selector backing API. The dispatch (sub-agent)
// registry is a SEPARATE module on the backend and gets its own
// frontend wrappers later — these helpers must NEVER be reused
// for sub-agent infra.

// "local" = the local model (Nemotron) served by Ollama; keyless, never
// connectable/disconnectable.
export type ChatProviderName = "local" | "openrouter" | "anthropic" | "openai" | "gemini";

export type ChatProviderStatus = Record<string, { connected: boolean; needs_reconnect?: boolean }>;

export type ChatProviderStore = {
  /** "dpapi" on Windows with encryption working; "identity" if encryption
   *  fell back (non-Windows or a broken host). */
  backend:   string;
  encrypted: boolean;
};

export type ChatProviderStatusResponse = {
  providers: ChatProviderStatus;
  store:     ChatProviderStore;
};

export type ChatModelEntry = {
  provider: ChatProviderName | string;
  model_id: string;
  display_name: string;
};

export async function getChatProviderStatus(): Promise<ChatProviderStatusResponse> {
  const r = await apiFetch(`${BASE}/chat-providers/status`);
  if (!r.ok) throw new Error(`chat-providers/status ${r.status}`);
  return r.json();
}

export async function getChatProviderModels(): Promise<{ items: ChatModelEntry[] }> {
  const r = await apiFetch(`${BASE}/chat-providers/models`);
  if (!r.ok) throw new Error(`chat-providers/models ${r.status}`);
  return r.json();
}

export async function connectChatProvider(
  provider: Exclude<ChatProviderName, "local">,
  key: string,
): Promise<{ ok: true; models: { id: string; display_name: string }[] }> {
  const r = await apiFetch(`${BASE}/chat-providers/${provider}/connect`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!r.ok) {
    let detail = `${provider}/connect ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function disconnectChatProvider(
  provider: Exclude<ChatProviderName, "local">,
): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${BASE}/chat-providers/${provider}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`disconnect ${provider} ${r.status}`);
  return r.json();
}


// ── Dispatch providers (OpenRouter sub-agent infra) ──────────────────
// SEPARATE from chat providers — different registry, different endpoints,
// different storage. OpenRouter NEVER appears in the chat selector.

export type DispatchProviderStatus = Record<string, { connected: boolean; needs_reconnect?: boolean }>;

export type DispatchModelEntry = {
  id: string;
  display_name: string;
  tier: "fast" | "smart" | "other";
};

export async function getDispatchProviderStatus(): Promise<{
  providers: DispatchProviderStatus;
  store: { backend: string; encrypted: boolean };
}> {
  const r = await apiFetch(`${BASE}/dispatch-providers/status`);
  if (!r.ok) throw new Error(`dispatch-providers/status ${r.status}`);
  return r.json();
}

export async function getDispatchProviderModels(): Promise<{ items: DispatchModelEntry[] }> {
  const r = await apiFetch(`${BASE}/dispatch-providers/models`);
  if (!r.ok) throw new Error(`dispatch-providers/models ${r.status}`);
  return r.json();
}

export async function connectDispatchProvider(
  provider: "openrouter",
  key: string,
): Promise<{ ok: true; models: DispatchModelEntry[] }> {
  const r = await apiFetch(`${BASE}/dispatch-providers/${provider}/connect`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!r.ok) {
    let detail = `${provider}/connect ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function disconnectDispatchProvider(
  provider: "openrouter",
): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${BASE}/dispatch-providers/${provider}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`disconnect dispatch ${provider} ${r.status}`);
  return r.json();
}

export type SubAgentCallbacks = {
  onDelta: (chunk: string) => void;
  onDone?: (model: string, jobId: string) => void;
  onError?: (msg: string) => void;
};

function _humanizeDispatchError(raw: string): string {
  if (raw.includes("429")) return "OpenRouter rate-limited (429) — try again in a moment";
  if (raw.includes("timeout") || raw.includes("Timeout")) return "OpenRouter timeout — try again";
  if (raw.includes("401") || raw.includes("403")) return "OpenRouter auth error — check your key in Settings → Sub-agent infra";
  if (raw.includes("not connected")) return "OpenRouter not connected — go to Settings → Sub-agent infra";
  if (raw.includes("503") || raw.includes("502")) return "OpenRouter unavailable (503) — try again in a moment";
  return raw;
}

// Dispatch runs fire-and-forget (no AbortSignal) so the request completes
// independently of React lifecycle. SubAgentTile subscribes via module-level
// listeners, not React state tied to a signal.
export async function postDispatchSubagent(
  role: string,
  task: string,
  jobId: string,
  { onDelta, onDone, onError }: SubAgentCallbacks,
  modelTier: "fast" | "smart" = "fast",
  missionId?: string | null,
): Promise<void> {
  let r: Response;
  try {
    r = await apiFetch(`${BASE}/dispatch/subagent`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ role, task, model_tier: modelTier, job_id: jobId, mission_id: missionId ?? null }),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    onError?.(_humanizeDispatchError(msg));
    return;
  }
  if (!r.ok || !r.body) {
    let detail = `dispatch/subagent HTTP ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    onError?.(_humanizeDispatchError(detail));
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl = buf.indexOf("\n");
    while (nl !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) {
        try {
          const obj = JSON.parse(line);
          if (typeof obj.delta === "string") onDelta(obj.delta);
          else if (obj.error) onError?.(_humanizeDispatchError(String(obj.error)));
          else if (obj.done) onDone?.(String(obj.model || ""), String(obj.job_id || ""));
        } catch {}
      }
      nl = buf.indexOf("\n");
    }
  }
}

// ── Mission Control ──────────────────────────────────────────────────────────

export type MissionPlanSubtask = {
  id: string;
  role: string;
  task: string;
  tier: "fast" | "smart";
  est_tokens: number;
  depends_on?: string[];
};
export type MissionPlan = { title: string; subtasks: MissionPlanSubtask[] };

export type SpendSummary = {
  month: string;
  total_usd: number;
  by_provider: Record<string, number>;
  by_mission: Record<string, number>;
  cap_usd: number | null;
  over_cap: boolean;
  warn: boolean;
};

export async function getSpendSummary(): Promise<SpendSummary> {
  const r = await apiFetch(`${BASE}/spend/summary`);
  if (!r.ok) throw new Error(`spend/summary ${r.status}`);
  return (await r.json()) as SpendSummary;
}

export async function postSpendCap(usd: number | null): Promise<SpendSummary> {
  const r = await apiFetch(`${BASE}/spend/cap`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ usd }),
  });
  if (!r.ok) throw new Error(`spend/cap ${r.status}`);
  return (await r.json()) as SpendSummary;
}

export async function postMissionPlan(goal: string, model: string): Promise<MissionPlan> {
  const r = await apiFetch(`${BASE}/mission/plan`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ goal, model }),
  });
  if (!r.ok) {
    let detail = `mission/plan ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return (await r.json()) as MissionPlan;
}

// ── Narrator observer ────────────────────────────────────────────────────────

export type NarratorResponse = {
  advice: string;
  confidence: number;
  emotional_state: string;
  boundary: boolean;
  boundary_text: string | null;
  boundary_memory_id: string | null;
};

export async function postNarratorObserve(
  userTurn: string,
  novaReply: string,
): Promise<NarratorResponse> {
  const r = await apiFetch(`${BASE}/narrator/observe`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ user_turn: userTurn, nova_reply: novaReply }),
  });
  if (!r.ok) throw new Error(`narrator/observe ${r.status}`);
  return r.json();
}

// ── Nova permissions ─────────────────────────────────────────────────

export async function getNovaPerm(): Promise<{ permissions: Record<string, string> }> {
  const r = await apiFetch(`${BASE}/nova/permissions`);
  if (!r.ok) throw new Error(`nova/permissions ${r.status}`);
  return r.json();
}

export async function setNovaPerm(tool: string, level: "always" | "revoke"): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${BASE}/nova/permissions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ tool, level }),
  });
  if (!r.ok) throw new Error(`nova/permissions set ${r.status}`);
  return r.json();
}

export async function deleteNovaPerm(tool: string): Promise<{ ok: boolean }> {
  const r = await apiFetch(`${BASE}/nova/permissions/${encodeURIComponent(tool)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`nova/permissions delete ${r.status}`);
  return r.json();
}


export async function spotifySdkToken(): Promise<string> {
  const r = await apiFetch(`${BASE}/spotify/sdk-token`);
  if (!r.ok) throw new Error(`sdk-token ${r.status}`);
  const b = await r.json();
  return b.access_token as string;
}

// ── Weather — Open-Meteo (no auth) ─────────────────────────────────────────
export type WeatherCurrent = {
  temp_c: number | null;
  feels_like_c: number | null;
  humidity_pct: number | null;
  wind_kmh: number | null;
  condition: string;
  weather_code: number | null;
};
export type WeatherDay = {
  date: string;
  high_c: number | null;
  low_c: number | null;
  condition: string;
  precip_pct: number | null;
};
export type WeatherData = {
  city: string;
  country: string;
  region: string;
  latitude: number;
  longitude: number;
  timezone: string;
  current: WeatherCurrent;
  daily: WeatherDay[];
  fetched_at: string;
};

export async function getWeather(city: string): Promise<WeatherData> {
  const r = await apiFetch(`${BASE}/weather?city=${encodeURIComponent(city)}`);
  if (!r.ok) {
    let detail = `weather ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

// ── Weather, fetched DIRECTLY from Open-Meteo (client-side) ─────────────────
// Both endpoints are keyless + CORS-enabled, so the webview can call them
// without the backend hop — the tile no longer depends on the backend's
// network path. Backend /weather stays as the fallback (it also serves Nova's
// daily-briefing reader).

// WMO weather codes -> readable condition (mirror of the backend's table).
const WMO: Record<number, string> = {
  0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
  45: "Fog", 48: "Rime fog",
  51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
  56: "Freezing drizzle", 57: "Dense freezing drizzle",
  61: "Light rain", 63: "Rain", 65: "Heavy rain",
  66: "Freezing rain", 67: "Heavy freezing rain",
  71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
  80: "Light showers", 81: "Showers", 82: "Violent showers",
  85: "Snow showers", 86: "Heavy snow showers",
  95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ heavy hail",
};
const wmoName = (code: unknown): string =>
  typeof code === "number" && code in WMO ? WMO[code] : "Unknown";

export async function getWeatherDirect(city: string, signal?: AbortSignal): Promise<WeatherData> {
  // 1) geocode
  const geoUrl = "https://geocoding-api.open-meteo.com/v1/search?" + new URLSearchParams({
    name: city, count: "1", language: "en", format: "json",
  });
  const geoR = await fetch(geoUrl, { signal });
  if (!geoR.ok) throw new Error(`Weather service unreachable (geocoding ${geoR.status})`);
  const geo = await geoR.json();
  const loc = (geo?.results ?? [])[0];
  if (!loc) throw new Error(`City not found: ${city}`);

  // 2) forecast
  const fcUrl = "https://api.open-meteo.com/v1/forecast?" + new URLSearchParams({
    latitude: String(loc.latitude), longitude: String(loc.longitude),
    current: "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m,apparent_temperature",
    daily: "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    temperature_unit: "celsius", wind_speed_unit: "kmh",
    timezone: "auto", forecast_days: "7",
  });
  const fcR = await fetch(fcUrl, { signal });
  if (!fcR.ok) throw new Error(`Weather service unreachable (forecast ${fcR.status})`);
  const fc = await fcR.json();
  const cur = fc?.current ?? {};
  const daily = fc?.daily ?? {};
  const days: string[] = daily.time ?? [];

  const numOrNull = (v: unknown): number | null => (typeof v === "number" ? v : null);
  return {
    city: loc.name ?? city,
    country: loc.country ?? "",
    region: loc.admin1 ?? "",
    latitude: loc.latitude, longitude: loc.longitude,
    timezone: fc?.timezone ?? "UTC",
    current: {
      temp_c: numOrNull(cur.temperature_2m),
      feels_like_c: numOrNull(cur.apparent_temperature),
      humidity_pct: numOrNull(cur.relative_humidity_2m),
      wind_kmh: numOrNull(cur.wind_speed_10m),
      condition: wmoName(cur.weather_code),
      weather_code: numOrNull(cur.weather_code),
    },
    daily: days.slice(0, 7).map((date, i) => ({
      date,
      high_c: numOrNull((daily.temperature_2m_max ?? [])[i]),
      low_c: numOrNull((daily.temperature_2m_min ?? [])[i]),
      condition: wmoName((daily.weather_code ?? [])[i]),
      precip_pct: numOrNull((daily.precipitation_probability_max ?? [])[i]),
    })),
    fetched_at: new Date().toISOString(),
  };
}

// ── Weather fallback #2: wttr.in (keyless, global, CORS: * — verified) ──────
// Different infrastructure from Open-Meteo, so one provider's outage doesn't
// blank the tile. j1 gives current + 3 forecast days; mapped into the same
// WeatherData shape (3 days < Open-Meteo's 7, the tile renders what exists).

/* eslint-disable @typescript-eslint/no-explicit-any */
export async function getWeatherWttr(city: string, signal?: AbortSignal): Promise<WeatherData> {
  const r = await fetch(`https://wttr.in/${encodeURIComponent(city)}?format=j1`, { signal });
  if (!r.ok) throw new Error(`Weather service unreachable (wttr ${r.status})`);
  const d: any = await r.json();
  const cur = d?.current_condition?.[0];
  if (!cur) throw new Error(`City not found: ${city}`);
  const area = d?.nearest_area?.[0] ?? {};
  const num = (v: unknown): number | null => {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  const first = (x: any): string => String(x?.[0]?.value ?? "").trim();

  return {
    city: first(area.areaName) || city,
    country: first(area.country),
    region: first(area.region),
    latitude: num(area.latitude) ?? 0,
    longitude: num(area.longitude) ?? 0,
    timezone: "local",
    current: {
      temp_c: num(cur.temp_C),
      feels_like_c: num(cur.FeelsLikeC),
      humidity_pct: num(cur.humidity),
      wind_kmh: num(cur.windspeedKmph),
      condition: first(cur.weatherDesc) || "Unknown",
      weather_code: null,      // WWO codes ≠ WMO; the tile falls back to the condition text
    },
    daily: (d?.weather ?? []).map((w: any) => {
      const hourly: any[] = w?.hourly ?? [];
      const midday = hourly[Math.min(4, Math.max(0, hourly.length - 1))];
      const precip = hourly.length
        ? Math.max(...hourly.map((h) => num(h?.chanceofrain) ?? 0))
        : null;
      return {
        date: String(w?.date ?? ""),
        high_c: num(w?.maxtempC),
        low_c: num(w?.mintempC),
        condition: first(midday?.weatherDesc) || "Unknown",
        precip_pct: precip,
      };
    }),
    fetched_at: new Date().toISOString(),
  };
}
/* eslint-enable @typescript-eslint/no-explicit-any */

// ── Usage — per-provider summary ────────────────────────────────────────────
export type ChatProviderUsage = {
  connected: boolean;
  model_count: number;
  label: string;
};

export type OpenRouterUsage = {
  connected: boolean;
  usage_usd?: number | null;
  limit_usd?: number | null;
  is_free_tier?: boolean;
  rate_limit?: unknown;
};

export type UsageAll = {
  chat: Record<string, ChatProviderUsage>;
  dispatch: { openrouter: OpenRouterUsage };
};

export async function getUsageAll(): Promise<UsageAll> {
  const r = await apiFetch(`${BASE}/usage/all`);
  if (!r.ok) throw new Error(`usage ${r.status}`);
  return r.json();
}

// ── Local voice (faster-whisper + Kokoro, 100% on the backend box) ──────────

export type VoiceStatus = {
  stt: { installed: boolean; loaded: boolean; model: string; device: string };
  tts: { installed: boolean; loaded: boolean; files_present: boolean;
         downloading: boolean; progress_pct: number; error: string; voice: string };
};

export async function getVoiceStatus(): Promise<VoiceStatus> {
  const r = await apiFetch(`${BASE}/voice/status`);
  if (!r.ok) throw new Error(`voice/status ${r.status}`);
  return r.json();
}

export async function transcribeVoice(blob: Blob, filename?: string): Promise<{ text: string; latency_ms: number }> {
  const fd = new FormData();
  // Name by container so the backend decoder gets an honest extension hint
  // (native Android capture is m4a/AAC; the browser path is webm/opus).
  const name = filename || (blob.type.includes("mp4") ? "ptt.m4a" : "ptt.webm");
  fd.append("file", blob, name);
  const r = await apiFetch(`${BASE}/voice/transcribe`, { method: "POST", body: fd });
  if (!r.ok) {
    let detail = `transcribe ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

/** Returns WAV bytes for local playback. */
export async function speakText(text: string, voice?: string): Promise<ArrayBuffer> {
  const r = await apiFetch(`${BASE}/voice/speak`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text, voice }),
  });
  if (!r.ok) {
    let detail = `speak ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.arrayBuffer();
}

// ── Vault (markdown second brain) ────────────────────────────────────────────

export type VaultStats = { nodes: number; chunks: number; edges: number;
                           domains: number; chars: number; dir: string };
export type VaultNoteRef = { id: string; slug: string; title: string;
                             chars?: number; updated?: number };
export type VaultTreeNode = { children?: Record<string, VaultTreeNode>;
                              notes?: VaultNoteRef[] };
export type VaultGraph = {
  nodes: Array<{ id: string; slug: string; title: string; domain: string[];
                 chars: number; degree: number }>;
  edges: Array<{ src: string; dst: string; kind: string }>;
};
export type VaultHit = { node_id: string; slug: string; title: string;
                         domain: string[]; snippet: string; score: number };

export async function getVaultStats(): Promise<VaultStats> {
  const r = await apiFetch(`${BASE}/vault/stats`);
  if (!r.ok) throw new Error(`vault/stats ${r.status}`);
  return r.json();
}

export async function getVaultTree(): Promise<{ tree: Record<string, VaultTreeNode>; count: number }> {
  const r = await apiFetch(`${BASE}/vault/tree`);
  if (!r.ok) throw new Error(`vault/tree ${r.status}`);
  return r.json();
}

export async function getVaultGraph(): Promise<VaultGraph> {
  const r = await apiFetch(`${BASE}/vault/graph`);
  if (!r.ok) throw new Error(`vault/graph ${r.status}`);
  return r.json();
}

/** The Vault graph adapted to the RagGraph shape the Vision 3D view consumes.
 *  This is the unified knowledge source now (legacy /rag/graph is retired). */
export async function getKnowledgeGraph(): Promise<RagGraph> {
  const g = await getVaultGraph();
  return {
    nodes: g.nodes.map((n) => ({
      id: n.id,
      label: n.title,
      source: n.title,
      type: "doc",
      text: `${n.title}${n.domain?.length ? " — " + n.domain.join(" / ") : ""}`,
    })),
    links: g.edges.map((e) => ({ source: e.src, target: e.dst, value: 1 })),
  };
}

export async function vaultSearch(q: string, k = 10): Promise<{ items: VaultHit[] }> {
  const r = await apiFetch(`${BASE}/vault/search?q=${encodeURIComponent(q)}&k=${k}`);
  if (!r.ok) throw new Error(`vault/search ${r.status}`);
  return r.json();
}

export async function vaultQuery(question: string, opts: { k?: number; synthesize?: boolean } = {}):
  Promise<{ answer: string; sources: Array<{ node_id: string; slug: string; title: string; score: number }>; hits: number }> {
  const r = await apiFetch(`${BASE}/vault/query`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, k: opts.k ?? 8, synthesize: opts.synthesize ?? false }),
  });
  if (!r.ok) throw new Error(`vault/query ${r.status}`);
  return r.json();
}

export async function getVaultNode(idOrSlug: string): Promise<{
  id: string; slug: string; title: string; rel_path: string; domain: string[];
  tags: string[]; source: { kind: string; ref: string }; body: string;
}> {
  const r = await apiFetch(`${BASE}/vault/node/${encodeURIComponent(idOrSlug)}`);
  if (!r.ok) throw new Error(`vault/node ${r.status}`);
  return r.json();
}

export async function vaultIngest(input: { text?: string; url?: string; title?: string }):
  Promise<{ ok: boolean; node: { slug: string; title: string; domain: string[] } }> {
  const r = await apiFetch(`${BASE}/vault/ingest`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!r.ok) {
    let detail = `vault/ingest ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function vaultIngestFile(file: Blob, name: string):
  Promise<{ ok: boolean; kind: string; node: { slug: string; title: string; domain: string[] } }> {
  const fd = new FormData();
  fd.append("file", file, name);
  const r = await apiFetch(`${BASE}/vault/ingest/file`, { method: "POST", body: fd });
  if (!r.ok) {
    let detail = `vault/ingest/file ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function vaultReindex(): Promise<{ scanned: number; indexed: number; removed: number }> {
  const r = await apiFetch(`${BASE}/vault/reindex`, { method: "POST" });
  if (!r.ok) throw new Error(`vault/reindex ${r.status}`);
  return r.json();
}

// ── Skills ───────────────────────────────────────────────────────────────────

export type SkillInfo = { name: string; description: string; triggers: string[];
                          enabled: boolean; fired: number; chars: number };

export async function getSkills(): Promise<{ items: SkillInfo[] }> {
  const r = await apiFetch(`${BASE}/skills`);
  if (!r.ok) throw new Error(`skills ${r.status}`);
  return r.json();
}

export async function toggleSkill(name: string, enabled: boolean): Promise<{ items: SkillInfo[] }> {
  const r = await apiFetch(`${BASE}/skills/toggle`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, enabled }),
  });
  if (!r.ok) throw new Error(`skills/toggle ${r.status}`);
  return r.json();
}

export async function reindexSkills(): Promise<{ count: number }> {
  const r = await apiFetch(`${BASE}/skills/reindex`, { method: "POST" });
  if (!r.ok) throw new Error(`skills/reindex ${r.status}`);
  return r.json();
}

// ── Sensors (opt-in device telemetry) ────────────────────────────────────────

export type SensorKind = "geo" | "motion" | "light" | "battery" | "barometer";

export async function postSensor(device: string, kind: SensorKind,
                                 payload: Record<string, unknown>): Promise<void> {
  await apiFetch(`${BASE}/sensor/ingest`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ device, kind, payload }),
  });
}

export async function getSensorLatest(kinds?: string):
  Promise<{ items: Array<{ device: string; kind: SensorKind; payload: Record<string, unknown>; ts: number; age_s: number }> }> {
  const q = kinds ? `?kinds=${encodeURIComponent(kinds)}` : "";
  const r = await apiFetch(`${BASE}/sensor/latest${q}`);
  if (!r.ok) throw new Error(`sensor/latest ${r.status}`);
  return r.json();
}

// ── Mission runtime v2 (server-side DAG execution) ──────────────────────────

export type MissionNodeBrief = { id: string; status: string; attempts: number;
                                 error: string; model: string; distilled: string;
                                 output_chars: number };
export type MissionSnapshot = { id: string; title: string; goal: string;
                                status: string; nodes: MissionNodeBrief[];
                                synthesis: string; error: string };

export async function missionRun(input: {
  goal: string; title?: string; model?: string;
  subtasks: Array<{ id?: string; role: string; task: string; tier: string;
                    est_tokens?: number; depends_on?: string[] }>;
}): Promise<MissionSnapshot> {
  const r = await apiFetch(`${BASE}/mission/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!r.ok) {
    let detail = `mission/run ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function getMissionRecent(limit = 20): Promise<{ items: MissionSnapshot[] }> {
  const r = await apiFetch(`${BASE}/mission/recent?limit=${limit}`);
  if (!r.ok) throw new Error(`mission/recent ${r.status}`);
  return r.json();
}

export async function missionStop(id: string): Promise<{ stopped: boolean }> {
  const r = await apiFetch(`${BASE}/mission/${encodeURIComponent(id)}/stop`, { method: "POST" });
  if (!r.ok) throw new Error(`mission/stop ${r.status}`);
  return r.json();
}

/** NDJSON subscription: onState fires per snapshot until a terminal status. */
export async function missionEvents(
  id: string,
  onState: (s: MissionSnapshot) => void,
  onError?: (msg: string) => void,
): Promise<void> {
  let r: Response;
  try {
    r = await apiFetch(`${BASE}/mission/${encodeURIComponent(id)}/events`);
  } catch (e) {
    onError?.(e instanceof Error ? e.message : String(e));
    return;
  }
  if (!r.ok || !r.body) {
    onError?.(`mission/events ${r.status}`);
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.state) onState(obj.state as MissionSnapshot);
        } catch { /* skip malformed line */ }
      }
    }
  } catch (e) {
    onError?.(e instanceof Error ? e.message : String(e));
  }
}

// ── Paper trader (simulation only — real prices, fake money) ─────────────────

export type TraderStrategy = { name: string; label: string; params: Record<string, unknown> };
export type TraderTrade = { side: string; bar: number; price: number; shares: number };
export type BacktestResult = {
  id: string; symbol: string; strategy: string; bars: number;
  start_cash: number; final_equity: number; total_return_pct: number;
  sharpe: number; max_drawdown_pct: number; win_rate_pct: number;
  num_trades: number; equity_curve: number[]; trades: TraderTrade[];
};

export async function getTraderStrategies(): Promise<{ items: TraderStrategy[] }> {
  const r = await apiFetch(`${BASE}/trader/strategies`);
  if (!r.ok) throw new Error(`trader/strategies ${r.status}`);
  return r.json();
}

export async function runBacktest(input: {
  symbol: string; strategy: string; days?: number; start_cash?: number;
  params?: Record<string, unknown>;
}): Promise<BacktestResult> {
  const r = await apiFetch(`${BASE}/trader/backtest`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!r.ok) {
    let detail = `trader/backtest ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function getTraderHistory(limit = 20): Promise<{
  items: Array<{ id: string; ts: number; symbol: string; strategy: string;
                 metrics: Record<string, number>; trades: number }>;
}> {
  const r = await apiFetch(`${BASE}/trader/history?limit=${limit}`);
  if (!r.ok) throw new Error(`trader/history ${r.status}`);
  return r.json();
}

// ── Trader v2 — autonomous portfolio (paper, real prices) ────────────────────

export type TraderPosition = { symbol: string; shares: number; avg_cost: number };
export type TraderAccount = {
  cash: number; start_cash: number; equity: number;
  total_return_pct: number; max_drawdown_pct: number; sharpe: number;
  positions: TraderPosition[]; equity_curve: number[]; universe: string[];
  config: { top_n: number; cash_reserve_pct: number; min_score: number };
  inception: number; updated: number;
};
export type AutopilotResult = {
  equity: number; cash: number; targets: string[];
  scores: Record<string, number>;
  fills: Array<{ symbol: string; side: string; shares: number; price: number }>;
  positions: Array<{ symbol: string; shares: number; avg_cost: number; price: number; value: number; pnl_pct: number }>;
};

export async function getTraderAccount(): Promise<TraderAccount> {
  const r = await apiFetch(`${BASE}/trader/account`);
  if (!r.ok) throw new Error(`trader/account ${r.status}`);
  return r.json();
}

export async function traderStep(): Promise<AutopilotResult> {
  const r = await apiFetch(`${BASE}/trader/autopilot/step`, { method: "POST" });
  if (!r.ok) {
    let detail = `autopilot ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function setTraderUniverse(symbols: string[]): Promise<{ ok: boolean; universe: string[] }> {
  const r = await apiFetch(`${BASE}/trader/universe`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ symbols }),
  });
  if (!r.ok) throw new Error(`trader/universe ${r.status}`);
  return r.json();
}

export async function setTraderConfig(cfg: { top_n?: number; cash_reserve_pct?: number; min_score?: number }):
  Promise<{ ok: boolean; config: Record<string, number> }> {
  const r = await apiFetch(`${BASE}/trader/autopilot/config`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(cfg),
  });
  if (!r.ok) throw new Error(`trader/config ${r.status}`);
  return r.json();
}

export async function traderReset(cash: number): Promise<{ ok: boolean; cash: number }> {
  const r = await apiFetch(`${BASE}/trader/reset`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ cash }),
  });
  if (!r.ok) throw new Error(`trader/reset ${r.status}`);
  return r.json();
}

// ── Trader Agent — autonomous portfolio manager (reasoned, paper only) ───────

export type AgentPick = {
  symbol: string; target_pct: number; conviction: string; thesis: string; risks: string;
};
export type AgentRiskStatus = {
  halted: boolean; drawdown_pct: number; invested_pct?: number; cash_pct?: number;
  capped: string[]; dropped_names: string[]; sector_scaled: Record<string, number>;
  limits: Record<string, number>;
};
export type AgentMeta = { attempts?: number; fallback?: boolean; evaluated?: boolean; reasons?: string[] };
export type AgentOrder = { symbol: string; side: string; usd: number };
export type AgentFill = { symbol: string; side: string; shares: number; price: number };
export type AgentRunResult = {
  id: string; ts: number; equity_before: number; equity_after: number; cash_after: number;
  return_pct: number; market_view: string; picks: AgentPick[]; meta: AgentMeta;
  orders: AgentOrder[]; fills: AgentFill[]; risk_status: AgentRiskStatus;
  positions: Array<{ symbol: string; shares: number; avg_cost: number; price: number; value: number; pnl_pct: number }>;
  macro: Record<string, unknown>; sentiment: AgentSentiment;
};
export type AgentSentiment = "bullish" | "neutral" | "bearish";
export type AgentDecision = {
  id: string; ts: number; equity_before: number; equity_after: number; cash_after: number;
  market_view: string; market_snapshot: Record<string, unknown> | null; signals: unknown[] | null;
  rationale: { market_view: string; picks: AgentPick[]; meta?: AgentMeta } | null;
  risk_status: AgentRiskStatus | null; orders: AgentOrder[] | null; fills: AgentFill[] | null;
  forward_outcome: { portfolio_return_pct: number; held_days: number; per_name: Record<string, number> } | null;
  sentiment: AgentSentiment; summary: string;   // plain-English readout (backend-composed)
};
export type AgentConfig = {
  max_position_pct: number; sector_cap_pct: number; cash_floor_pct: number;
  max_names: number; max_drawdown_halt_pct: number; candidate_pool: number;
};

export async function traderAgentRun(): Promise<AgentRunResult> {
  const r = await apiFetch(`${BASE}/trader/agent/run`, { method: "POST" });
  if (!r.ok) {
    let detail = `trader/agent ${r.status}`;
    try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch {}
    throw new Error(detail);
  }
  return r.json();
}

export async function getTraderDecisions(limit = 50): Promise<{ items: AgentDecision[] }> {
  const r = await apiFetch(`${BASE}/trader/decisions?limit=${limit}`);
  if (!r.ok) throw new Error(`trader/decisions ${r.status}`);
  return r.json();
}

export async function getTraderAgentConfig(): Promise<{ config: AgentConfig }> {
  const r = await apiFetch(`${BASE}/trader/agent/config`);
  if (!r.ok) throw new Error(`trader/agent/config ${r.status}`);
  return r.json();
}

export async function setTraderAgentConfig(cfg: Partial<AgentConfig>): Promise<{ config: AgentConfig }> {
  const r = await apiFetch(`${BASE}/trader/agent/config`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(cfg),
  });
  if (!r.ok) throw new Error(`trader/agent/config ${r.status}`);
  return r.json();
}

/** Download the decision ledger with the Bearer attached, then save it via a
 *  temporary blob link. NEVER an `<a href>` to the authed endpoint — the
 *  WebView would navigate to a 401 JSON page and replace the whole SPA.
 *  fmt "md" = plain-English report; "csv" = the full raw heuristics dataset. */
export async function downloadTraderDecisions(fmt: "md" | "csv", limit = 500): Promise<void> {
  const r = await apiFetch(`${BASE}/trader/decisions?format=${fmt}&limit=${limit}`);
  if (!r.ok) throw new Error(`trader/decisions ${r.status}`);
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `trader_decisions.${fmt}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
