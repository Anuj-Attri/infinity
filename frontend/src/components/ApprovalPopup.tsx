// Pinned above the chat bubble — non-modal, unmissable.
// Shows when Nova wants to take a canvas action that isn't auto-approved.

import type { ParsedToolCall } from "../types";

type Props = {
  call: ParsedToolCall;
  onAllowOnce: () => void;
  onAllowAlways: () => void;
  onDecline: () => void;
};

export function humanDescription(call: ParsedToolCall): string {
  switch (call.tool) {
    case "spawn_tile":  return `spawn a ${call.type ?? "tile"} tile`;
    case "spawn_tiles": return `open tiles: ${(call.types ?? []).join(", ") || "several"}`;
    case "arrange_tiles": return `tidy the tiles`;
    case "close_tile":  return `close the ${call.id ?? "tile"} tile`;
    case "write_tile":  return `write to ${call.target ?? "a tile"}`;
    case "market_search": {
      const m = String(call.mode ?? "news");
      const q = call.query ? ` "${call.query}"` : "";
      return `search ${m === "movers" ? "today's movers" : m + q}`;
    }
    case "query_documents":
      return `search your documents${call.query ? `: "${call.query}"` : ""}`;
    case "dispatch_subagent":
      return `dispatch ${call.role ?? "sub-agent"}: "${call.task ?? ""}"`;
    case "dispatch_mission":
      return `plan a mission: "${call.goal ?? call.task ?? ""}"`;
    case "mcp_call": {
      const n = String(call.name ?? "?");
      if (n === "run_command") {
        const cmd = (call.args as Record<string, unknown> | undefined)?.command;
        return `run a shell command${cmd ? `: ${String(cmd).slice(0, 80)}` : ""}`;
      }
      return `call MCP tool "${n}"`;
    }
    default:
      return call.tool;
  }
}

export default function ApprovalPopup({ call, onAllowOnce, onAllowAlways, onDecline }: Props) {
  const desc = humanDescription(call);
  const isMcp = call.tool === "mcp_call";
  // For MCP, show EXACTLY what's being sent: server, tool, and the raw args —
  // so the user can catch a tool being called with exfiltrated/sensitive data.
  let argsJson = "";
  if (isMcp) {
    try { argsJson = JSON.stringify(call.args ?? {}, null, 2); } catch { argsJson = "{}"; }
  }
  return (
    <div className="approval-popup">
      <span className="approval-desc" title={`Nova wants to: ${desc}`}>
        <strong>Nova</strong> wants to: {desc}
      </span>
      {isMcp && (
        <div className="approval-mcp">
          <div className="approval-mcp-row"><span>server</span><code>{String(call.server_id ?? "?")}</code></div>
          <div className="approval-mcp-row"><span>tool</span><code>{String(call.name ?? "?")}</code></div>
          <div className="approval-mcp-args">
            <span>args (sent to the server):</span>
            <pre>{argsJson}</pre>
          </div>
        </div>
      )}
      <div className="approval-actions">
        <button type="button" className="approval-btn approval-btn-allow"  onClick={onAllowOnce}>Allow once</button>
        {/* MCP tools are default-deny: NO "Always" — every call is asked. */}
        {!isMcp && (
          <button type="button" className="approval-btn approval-btn-always" onClick={onAllowAlways}>Always</button>
        )}
        <button type="button" className="approval-btn approval-btn-decline" onClick={onDecline}>Decline</button>
      </div>
    </div>
  );
}
