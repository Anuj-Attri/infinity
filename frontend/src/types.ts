// Shared types for the conversation domain.

export type Role = "user" | "agent";

export type Message = {
  role: Role;
  text: string;
  ts: number;    // epoch ms
  model?: string;
};

// A tool call parsed from a <nova_action>…</nova_action> block in Nova's reply.
export type ParsedToolCall = {
  tool: string;
  // spawn_tile
  type?: string;
  // close_tile / read_tile
  id?: string;
  // read_tiles / spawn_tiles
  ids?: string[];
  types?: string[];
  // write_tile
  target?: string;          // "notes" | "tasks"
  content?: string;
  // market_search
  mode?: string;            // "movers" | "ticker" | "news"
  query?: string;
  // dispatch_subagent
  role?: string;
  task?: string;
  model_tier?: "fast" | "smart";
  // all
  [key: string]: unknown;
};
