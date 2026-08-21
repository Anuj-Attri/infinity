// Slash-COMMAND registry — distinct from the slash-TILE-REFERENCE registry
// (tileRegistry.ts). A slash-command is the leading VERB of a chat message
// (e.g. `/summarize`) that triggers a workflow; it must NOT be resolved as a
// tile reference. The /-tokens that FOLLOW a command (`/summarize /notes`) are
// still tile references and resolve through tileRegistry as normal.
//
// This is the template for future workflow commands (/image, /audio):
// add an entry here and an intercept in App.startStream. Autocomplete + the
// command-vs-reference parsing pick it up automatically.

export type SlashCommand = {
  key: string;          // bare keyword, e.g. "summarize" (also the /token)
  label: string;        // shown in autocomplete, e.g. "/summarize"
  description: string;  // terse tagline (1-3 words)
};

export const SLASH_COMMANDS: SlashCommand[] = [
  { key: "summarize", label: "/summarize", description: "Summarize tiles" },
  { key: "research",  label: "/research",  description: "Source-check a topic" },
  { key: "plan",      label: "/plan",      description: "Build a milestone plan" },
  { key: "refresh",   label: "/refresh",   description: "Re-ground on now" },
  { key: "remember",  label: "/remember",  description: "Add text/link to brain" },
  { key: "feed",      label: "/feed",      description: "Add text/link to brain" },
  { key: "image",     label: "/image",     description: "Generate image" },
  { key: "audio",     label: "/audio",     description: "Generate music" },
  { key: "animate",   label: "/animate",   description: "Motion-graphics video (local render)" },
];

const CMD_KEYS = new Set(SLASH_COMMANDS.map((c) => c.key));

/** True if `key` (bare, no slash) is a recognized slash-command keyword. */
export function isSlashCommand(key: string): boolean {
  return CMD_KEYS.has(key.toLowerCase());
}
