// Workflow-chaining engine — pure parse + types (no React, no dispatch).
//
// A message can hold several /-commands that run as a SEQUENTIAL pipeline,
// each step's output feeding the next. The chain ONLY fires from explicit
// /-commands (the gate is always the user — Nova never self-initiates). This
// module just turns the raw message into ordered steps and answers the
// dependency questions the orchestrator (App.runChain) needs for its
// failure-judgment. The actual execution + data handoff lives in App, reusing
// the existing skills (/research, /summarize, /image, /audio) as primitives.

import { isSlashCommand } from "./slashCommands";

export type ChainStep = { cmd: string; args: string };

// A step's product, carried to the next step as its implicit input.
export type ChainArtifact = {
  kind: "text" | "image" | "audio";
  text?: string;     // for text artifacts (research/summary) OR a media caption
  dataUrl?: string;  // for image/audio artifacts
  tileId: string;
  label: string;
};

export const CHAIN_LABELS: Record<string, string> = {
  research: "Research", summarize: "Summary", image: "Image", audio: "Audio",
};

const SLASH_RE = /(^|\s)\/([a-zA-Z][\w-]*)/g;

/** Split a message into ordered command-steps. Splits ONLY on registered
 *  command keywords (isSlashCommand) — tile-references like /notes stay inside
 *  their step's args. Returns [] unless the message STARTS with a command (so
 *  ordinary chat that merely mentions a slash never becomes a chain). */
export function parseChain(message: string): ChainStep[] {
  const t = message.trim();
  const marks: { cmd: string; slashAt: number; argsAt: number }[] = [];
  let m: RegExpExecArray | null;
  SLASH_RE.lastIndex = 0;
  while ((m = SLASH_RE.exec(t)) !== null) {
    const cmd = m[2].toLowerCase();
    if (!isSlashCommand(cmd)) continue;          // tile-ref, not a command → not a split point
    marks.push({ cmd, slashAt: m.index + m[1].length, argsAt: SLASH_RE.lastIndex });
  }
  // Must be a command message: the first command sits at the very start.
  if (!marks.length || marks[0].slashAt !== 0) return [];
  const steps: ChainStep[] = [];
  for (let i = 0; i < marks.length; i++) {
    const end = i + 1 < marks.length ? marks[i + 1].slashAt : t.length;
    steps.push({ cmd: marks[i].cmd, args: t.slice(marks[i].argsAt, end).trim() });
  }
  return steps;
}

/** Tile-references inside a step's args (slash tokens that are NOT commands). */
export function stepRefs(args: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  const re = /\/([a-zA-Z][\w-]*)/g;
  while ((m = re.exec(args)) !== null) {
    const id = m[1].toLowerCase();
    if (isSlashCommand(id)) continue;
    if (!seen.has(id)) { seen.add(id); out.push(id); }
  }
  return out;
}

/** Free text of a step's args (slash-refs stripped). */
export function stepFreeText(args: string): string {
  return args.replace(/\/[a-zA-Z][\w-]*/g, " ").replace(/\s+/g, " ").trim();
}

/** A step "consumes the prior" when it carries NO explicit tile-reference of
 *  its own — i.e. its subject is whatever the previous step produced. */
export function consumesPrior(step: ChainStep): boolean {
  return stepRefs(step.args).length === 0;
}

/** FAILURE JUDGMENT (the anti-leaky-pipe): a step's failure is CRITICAL when
 *  the NEXT step would run on its (now-missing) output — i.e. the next step
 *  consumes the prior. Then we STOP + gate. Otherwise it's MINOR (the last
 *  step, or a next step that has its own explicit source) → skip + note. */
export function isCriticalFailure(steps: ChainStep[], idx: number): boolean {
  const next = steps[idx + 1];
  return !!next && consumesPrior(next);
}
