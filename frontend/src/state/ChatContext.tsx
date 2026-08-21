// Shared chat state — transcript, pending stream, model + no_think settings,
// submit handler. Lets the ChatTile (inside the tile grid) consume the same
// chat state as the rest of the app without prop-drilling through TileGrid.

import { createContext, useContext, type ReactNode } from "react";
import type { Message } from "../types";

export type ChatState = {
  transcript: Message[];
  pending: { text: string; model: string } | null;
  busy: boolean;
  error: string | null;
  onSubmit: (text: string) => void;
  /** Spoken turn (PTT): sends with the voice contract and resolves with the
   *  final reply text for local TTS playback. */
  onSubmitVoice: (text: string) => Promise<string>;

  modelId: string;
  setModelId: (id: string) => void;
  noThink: boolean;
  setNoThink: (v: boolean) => void;

  /** Re-ask the last question on the cloud default (Sonnet) — the one-tap
   *  safety valve when a LOCAL reply misses the nuance. */
  escalateLast: () => void;
};

const ChatCtx = createContext<ChatState | null>(null);

export function ChatProvider({ value, children }: { value: ChatState; children: ReactNode }) {
  return <ChatCtx.Provider value={value}>{children}</ChatCtx.Provider>;
}

export function useChat(): ChatState {
  const v = useContext(ChatCtx);
  if (!v) throw new Error("useChat called outside ChatProvider");
  return v;
}
