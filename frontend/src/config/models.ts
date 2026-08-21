// Chat model registry — dynamic.
//
// Post-OpenRouter migration: ALL language runs on OpenRouter. The live model
// list comes from `GET /chat-providers/models`, populated by:
//   * OpenRouter's full catalog (the primary path — searchable in the selector),
//   * any premium BYOK provider the user has connected (Anthropic/OpenAI/Gemini),
//   * a single always-present LOCAL fallback entry (a small CPU model used only
//     when OpenRouter is over its spend cap or unreachable — never the GPU).
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

// Auto (local-first) — the backend resolves this per turn: daily/easy turns
// run on the LOCAL Gemma 4 12B GPU brain (zero cloud spend, on-demand
// load/unload); hard/long/heavy-coding turns ride the API default above.
export const AUTO_MODEL_ID = "infinity/auto";
// Local GPU brain (explicit pick — waits for the model load if cold).
export const LOCAL_GEMMA_MODEL_ID = "local/gemma-4-12b";

// Local CPU fallback tag (the Ollama model served CPU-only). Authoritative id
// comes from /chat-providers/models (reactor/providers/ollama_local.py ::
// DEFAULT_MODEL); this is only the pre-fetch placeholder.
export const LOCAL_FALLBACK_MODEL_ID = "llama3.2:3b";

// The app-wide default selection: local-first auto routing.
export const DEFAULT_MODEL_ID = AUTO_MODEL_ID;

// Retired ids — migrated to DEFAULT_MODEL_ID if found in a user's storage
// (old local brains that no longer load on the GPU-free backend).
export const RETIRED_LOCAL_MODEL_IDS = [
  "qwen3:14b",
  "nemotron-local",
  "hf.co/bartowski/nvidia_NVIDIA-Nemotron-Nano-12B-v2-GGUF:Q6_K",
];

// Fallback used until the first /chat-providers/models response lands — keeps
// the selector populated and the default id resolvable pre-fetch.
export const FALLBACK_MODELS: ModelDef[] = [
  {
    id: AUTO_MODEL_ID,
    label: "Auto (local-first)",
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
  {
    id: LOCAL_FALLBACK_MODEL_ID,
    label: "Local fallback (CPU)",
    provider: "local",
    supportsNoThink: true,
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

export const PROVIDER_LABEL: Record<string, string> = {
  auto:          "Auto",
  "local-gemma": "Local GPU",
  openrouter:    "OpenRouter",
  anthropic:     "Anthropic",
  openai:        "OpenAI",
  gemini:        "Gemini",
  local:         "Local fallback",
};

// Auto first, then the local GPU brain, OpenRouter, BYOK, CPU fallback last.
export const PROVIDER_ORDER: string[] = ["auto", "local-gemma", "openrouter", "anthropic", "openai", "gemini", "local"];

// Legacy export — kept so any straggling import compiles. New code should
// pull the live list from ChatModelsContext instead.
export const MODELS: ModelDef[] = FALLBACK_MODELS;
