// Chat model registry — dynamic.
//
// The live list comes from `GET /chat-providers/models`, which merges:
//   * the models the product provides (the primary path, searchable here),
//   * any provider the user connected with their own key,
//   * one always-present CPU fallback, used only when the primary lane is over
//     its spend cap or unreachable.
//
// Which upstream carries a request is a ROUTING concern and stays on the
// backend. Nothing user-facing in this file names a router: the selector groups
// by who MAKES the model (see the vendor helpers below), because that is the
// distinction a person choosing a model actually cares about.
//
// The dispatch (sub-agent / generative) registry is separate by design and
// does NOT appear here.

import type { ChatModelEntry, ChatProviderName } from "../api";

export type ModelDef = {
  id: string;
  label: string;
  provider: ChatProviderName | string;
  supportsNoThink: boolean;
  available: boolean;
};

// Default ONLINE model — the everyday best-quality driver (bounded by the
// OpenRouter monthly spend cap). The user can switch to any catalog model in
// the searchable selector. Override server-side via OPENROUTER_CHAT defaults.
// Sonnet 5: 1M context, intro $2/$10 per Mtok through 2026-08-31 (then 3/15).
export const DEFAULT_ONLINE_MODEL_ID = "anthropic/claude-sonnet-5";

// The routing sentinel. The backend resolves it per turn.
//
// It was labelled "Auto (local-first)" because a local Gemma brain took the
// easy turns. There is no local lane in a hosted build, so that label described
// behaviour that cannot happen. The sentinel stays — it is the seam a cheap /
// strong split plugs into — but it no longer claims to be local.
export const AUTO_MODEL_ID = "infinity/auto";

// The app-wide default selection.
export const DEFAULT_MODEL_ID = AUTO_MODEL_ID;

// Retired ids — migrated to DEFAULT_MODEL_ID if found in a user's storage, so
// an existing install does not sit pinned to a model that cannot answer.
export const RETIRED_LOCAL_MODEL_IDS = [
  "local/gemma-4-12b",
  "llama3.2:3b",
  "qwen3:14b",
  "nemotron-local",
  "hf.co/bartowski/nvidia_NVIDIA-Nemotron-Nano-12B-v2-GGUF:Q6_K",
];

// Fallback used until the first /chat-providers/models response lands — keeps
// the selector populated and the default id resolvable pre-fetch.
export const FALLBACK_MODELS: ModelDef[] = [
  {
    id: AUTO_MODEL_ID,
    label: "Auto",
    provider: "auto",
    supportsNoThink: false,
    available: true,
  },
  {
    id: DEFAULT_ONLINE_MODEL_ID,
    label: "Claude Sonnet 5",
    provider: "openrouter",
    supportsNoThink: false,
    available: true,
  },
];

// Providers that honor the Think toggle. Only the local fallback maps the
// toggle onto a reasoning convention; OpenRouter + BYOK models reason natively
// and ignore it (the Fast/Thinking pill is a no-op for them).
const THINK_TOGGLE_PROVIDERS = new Set(["local"]);

export function modelDefFromEntry(e: ChatModelEntry): ModelDef {
  return {
    id: e.model_id,
    label: e.display_name || e.model_id,
    provider: e.provider,
    supportsNoThink: THINK_TOGGLE_PROVIDERS.has(e.provider),
    available: true,
  };
}

// `PROVIDER_LABEL` and `PROVIDER_ORDER` were here. They mapped our ROUTING
// lanes to display strings, which is what put a section header reading
// "OPENROUTER" above the model list. Replaced by the vendor helpers below;
// nothing imports them any more.


// ── Vendors ──────────────────────────────────────────────────────────────────
//
// The selector groups by who MAKES the model, not by how we reach it.
//
// `provider` is our routing lane, and grouping on it put every model on earth
// under one heading called "OPENROUTER" — which named our wholesaler, told the
// user nothing, and collapsed a genuinely useful distinction (Anthropic vs
// OpenAI vs Google) into a single undifferentiated list. Whether a request goes
// out through OpenRouter, a BYOK key, or the CPU fallback is our plumbing; a
// person choosing a model wants the maker's name.
//
// A side effect worth keeping: a BYOK Anthropic key and OpenRouter's
// `anthropic/*` slugs now land in the SAME group, because from the user's side
// they are the same thing — Claude — reached two different ways.

export const VENDOR_LABEL: Record<string, string> = {
  auto:          "Auto",
  anthropic:     "Anthropic",
  openai:        "OpenAI",
  google:        "Google",
  gemini:        "Google",
  "meta-llama":  "Meta",
  deepseek:      "DeepSeek",
  qwen:          "Qwen",
  moonshotai:    "Moonshot AI",
  mistralai:     "Mistral",
  "x-ai":        "xAI",
  cohere:        "Cohere",
  perplexity:    "Perplexity",
  nvidia:        "NVIDIA",
  microsoft:     "Microsoft",
  "local-gemma": "Local GPU",
  local:         "Local fallback",
};

// Vendors we want at the top, in this order. Anything else the catalog
// returns is appended alphabetically — so a new vendor appearing upstream
// shows up correctly without a code change, rather than vanishing from the
// selector because it was not on a hardcoded list.
export const VENDOR_PRIORITY: string[] = ["auto", "anthropic", "openai", "google"];

// Always last: the fallbacks are a safety net, not a choice you go looking for.
export const VENDOR_LAST: string[] = ["local-gemma", "local"];

/** Who made this model. Falls back to the routing lane when the id carries no
 *  vendor prefix (BYOK catalogs return bare ids like `claude-sonnet-5`). */
export function vendorOf(m: { id: string; provider: string }): string {
  if (m.provider === "auto" || m.provider === "local" || m.provider === "local-gemma") {
    return m.provider;
  }
  const slash = m.id.indexOf("/");
  if (slash > 0) return m.id.slice(0, slash).toLowerCase();
  return m.provider;
}

/** Display name for a vendor key, title-casing anything we don't know. */
export function vendorLabel(vendor: string): string {
  if (VENDOR_LABEL[vendor]) return VENDOR_LABEL[vendor];
  return vendor
    .split(/[-_]/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/** Vendor keys in display order: priority first, then unknowns A–Z, then
 *  fallbacks. Takes the vendors actually present so empty groups never render. */
export function orderVendors(present: Iterable<string>): string[] {
  const set = new Set(present);
  const out: string[] = [];
  for (const v of VENDOR_PRIORITY) if (set.delete(v)) out.push(v);
  const last: string[] = [];
  for (const v of VENDOR_LAST) if (set.delete(v)) last.push(v);
  out.push(...[...set].sort((a, b) => vendorLabel(a).localeCompare(vendorLabel(b))));
  out.push(...last);
  return out;
}

// Legacy export — kept so any straggling import compiles. New code should
// pull the live list from ChatModelsContext instead.
export const MODELS: ModelDef[] = FALLBACK_MODELS;
