// Ambient heartbeat — fire-and-forget visual signals picked up by LivingMesh.
// Never throws, never blocks; purely cosmetic.

export type PulseKind = "subagent" | "spawn";

export function emitPulse(kind: PulseKind): void {
  try {
    window.dispatchEvent(new CustomEvent("infinity:pulse", { detail: { kind } }));
  } catch { /* cosmetic only */ }
}

export function emitActivity(active: boolean): void {
  try {
    window.dispatchEvent(new CustomEvent("infinity:activity", { detail: { active } }));
  } catch { /* cosmetic only */ }
}
