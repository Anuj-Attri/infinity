// Live chat-model catalog. Wraps GET /chat-providers/models so the
// selector, ModelControls, and settings rows all read the same source.
//
// Refresh triggers: app mount, after a Connect/Disconnect in Settings,
// or a manual refresh() call from any consumer.
//
// The dispatch (sub-agent) registry is NOT exposed here — it has its own
// (forthcoming) context to keep the two cleanly separated.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  getChatProviderModels,
  getChatProviderStatus,
  type ChatModelEntry,
  type ChatProviderStatus,
  type ChatProviderStore,
} from "../api";
import {
  DEFAULT_MODEL_ID,
  FALLBACK_MODELS,
  modelDefFromEntry,
  type ModelDef,
} from "../config/models";

type Ctx = {
  models: ModelDef[];
  status: ChatProviderStatus;
  store:  ChatProviderStore;
  refresh: () => Promise<void>;
  loading: boolean;
};

const ChatModelsContext = createContext<Ctx | null>(null);

export function ChatModelsProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ChatModelEntry[]>([]);
  const [status, setStatus] = useState<ChatProviderStatus>({});
  const [store, setStore] = useState<ChatProviderStore>({ backend: "unknown", encrypted: false });
  const [loading, setLoading] = useState(true);
  const inflight = useRef(false);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const [m, s] = await Promise.all([
        getChatProviderModels().catch(() => ({ items: [] as ChatModelEntry[] })),
        getChatProviderStatus().catch(() => ({
          providers: {} as ChatProviderStatus,
          store: { backend: "unknown", encrypted: false } as ChatProviderStore,
        })),
      ]);
      setItems(m.items);
      setStatus(s.providers ?? {});
      setStore(s.store ?? { backend: "unknown", encrypted: false });
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const value: Ctx = useMemo(() => {
    const defs: ModelDef[] = items.length
      ? items.map(modelDefFromEntry)
      : FALLBACK_MODELS;

    // Ensure the default online model is present even if the backend momentarily
    // returned nothing (chat would otherwise have no usable default).
    const seen = new Set(defs.map((d) => d.id));
    if (!seen.has(DEFAULT_MODEL_ID)) defs.unshift(FALLBACK_MODELS[0]);

    // Grouping lives in the selector (ModelControls), which groups by VENDOR
    // rather than by routing lane. A second provider-keyed grouping used to be
    // computed here and consumed by nobody.
    return { models: defs, status, store, refresh, loading };
  }, [items, status, store, refresh, loading]);

  return <ChatModelsContext.Provider value={value}>{children}</ChatModelsContext.Provider>;
}

export function useChatModels(): Ctx {
  const v = useContext(ChatModelsContext);
  if (!v) throw new Error("useChatModels must be used inside ChatModelsProvider");
  return v;
}
