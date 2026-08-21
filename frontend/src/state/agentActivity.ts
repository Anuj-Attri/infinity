// Agent-activity event bus — the real feed behind the mobile "watch it build"
// surface (VoiceRemote's working view). App.tsx emits one event at each real
// point in the nova_action loop (stream start, tool proposed/running/result,
// consent gate, sub-agent dispatch, done, error); any surface can subscribe.
//
// Window CustomEvents (same pattern as infinity:pulse / infinity:voice) so
// there's no context threading and no coupling — a listener that isn't
// mounted just misses cosmetic events.

export type AgentToolPhase =
  | "proposed"        // Nova emitted a <nova_action> naming this tool
  | "running"         // the call is executing
  | "needs_approval"  // consent gate: waiting on the user
  | "approved"        // user allowed it
  | "denied"          // user declined it
  | "result";         // finished; `preview` holds a short result excerpt

export type AgentEvent =
  | { kind: "start"; goal: string }
  | { kind: "streaming" }                        // first token of a reply arrived
  | { kind: "reask" }                            // tool result injected, re-asking Nova
  | {
      kind: "tool";
      phase: AgentToolPhase;
      tool: string;                              // nova tool (mcp_call, spawn_tile, …)
      name?: string;                             // MCP tool name (read_text_file, …)
      server?: string;                           // MCP server id (hand-shell, …)
      args?: unknown;
      preview?: string;
    }
  | { kind: "subagent"; role: string; task: string }
  | { kind: "done"; text: string }
  | { kind: "error"; message: string };

const EVENT = "infinity:agent";

export function emitAgent(ev: AgentEvent): void {
  try {
    window.dispatchEvent(new CustomEvent(EVENT, { detail: ev }));
  } catch { /* cosmetic — never break the loop over a listener */ }
}

export function onAgent(cb: (ev: AgentEvent) => void): () => void {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent).detail as AgentEvent | undefined;
    if (detail && typeof detail.kind === "string") cb(detail);
  };
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}
