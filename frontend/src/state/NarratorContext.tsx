// Narrator — background observer that watches Nova's outputs and returns
// a structured note (advice, emotional state, confidence) fed silently into
// Nova's NEXT turn as an OBSERVER NOTE in the system block.
//
// Disabled by default (opt-in). Requires OpenRouter to be connected.
// Never shown to the user directly — pure backchannel.

import {
  createContext, useCallback, useContext, useMemo, useState, type ReactNode,
} from "react";

export type EmotionalState =
  | "neutral" | "playful" | "tired" | "frustrated"
  | "focused" | "excited" | "sad" | "anxious";

export type NarratorObservation = {
  advice: string;
  confidence: number;
  emotional_state: EmotionalState | string;   // backend-validated; string catches unknown values
  boundary: boolean;
  boundary_text: string | null;
  boundary_memory_id: string | null;
};

type Ctx = {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
  observation: NarratorObservation | null;
  setObservation: (o: NarratorObservation | null) => void;
};

const LS_KEY = "infinity.narrator.v1";
const NarratorCtx = createContext<Ctx | null>(null);

export function NarratorProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledRaw] = useState<boolean>(() => {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "false") === true; }
    catch { return false; }
  });
  const [observation, setObservation] = useState<NarratorObservation | null>(null);

  const setEnabled = useCallback((v: boolean) => {
    setEnabledRaw(v);
    try { localStorage.setItem(LS_KEY, JSON.stringify(v)); } catch {}
  }, []);

  const value = useMemo<Ctx>(
    () => ({ enabled, setEnabled, observation, setObservation }),
    [enabled, setEnabled, observation],
  );

  return <NarratorCtx.Provider value={value}>{children}</NarratorCtx.Provider>;
}

export function useNarrator(): Ctx {
  const v = useContext(NarratorCtx);
  if (!v) throw new Error("useNarrator outside NarratorProvider");
  return v;
}
