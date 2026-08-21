// Vault icon intelligence — infer a glyph + hue from a domain name or a
// note's source kind, so the knowledge graph reads as symbols, not text.
// Pure: stroke-based 24×24 SVG paths (lucide-flavored), zero deps.

import type { ReactNode } from "react";

// ── domain → icon (keyword-matched, first hit wins) ─────────────────────────
// "We believe in intelligence": the tree/graph never shows a bare folder — it
// infers what a domain *is* and shows that.

type IconDef = { paths: ReactNode; key: string };

const CHIP: IconDef = { key: "chip", paths: (<>
  <rect x="6" y="6" width="12" height="12" rx="1.5" />
  <path d="M9 9h6v6H9z" />
  <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
</>) };
const CODE: IconDef = { key: "code", paths: (<>
  <path d="M8 6l-5 6 5 6" /><path d="M16 6l5 6-5 6" />
</>) };
const GEAR: IconDef = { key: "gear", paths: (<>
  <circle cx="12" cy="12" r="3" />
  <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
</>) };
const ATOM: IconDef = { key: "atom", paths: (<>
  <circle cx="12" cy="12" r="1.6" />
  <ellipse cx="12" cy="12" rx="10" ry="4.2" />
  <ellipse cx="12" cy="12" rx="10" ry="4.2" transform="rotate(60 12 12)" />
  <ellipse cx="12" cy="12" rx="10" ry="4.2" transform="rotate(120 12 12)" />
</>) };
const BRAIN: IconDef = { key: "brain", paths: (<>
  <path d="M12 5a3 3 0 0 0-5.5 1.5A3 3 0 0 0 5 12a3 3 0 0 0 2 4.5A2.5 2.5 0 0 0 12 18z" />
  <path d="M12 5a3 3 0 0 1 5.5 1.5A3 3 0 0 1 19 12a3 3 0 0 1-2 4.5A2.5 2.5 0 0 1 12 18z" />
</>) };
const CHART: IconDef = { key: "chart", paths: (<>
  <path d="M4 20V4M4 20h16" /><path d="M8 16l3-4 3 2 4-6" />
</>) };
const PULSE: IconDef = { key: "pulse", paths: (<>
  <path d="M3 12h4l2-5 4 10 2-5h6" />
</>) };
const BOOK: IconDef = { key: "book", paths: (<>
  <path d="M5 4h11a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z" /><path d="M5 18h13" />
</>) };
const INBOX: IconDef = { key: "inbox", paths: (<>
  <path d="M4 13h4l1.5 3h5L16 13h4" /><path d="M4 13l2.5-8h11L20 13v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z" />
</>) };
const FOLDER: IconDef = { key: "folder", paths: (<>
  <path d="M3 7a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
</>) };
const GLOBE: IconDef = { key: "globe", paths: (<>
  <circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18" />
</>) };

const DOMAIN_RULES: Array<[RegExp, IconDef]> = [
  [/electron|hardware|circuit|micro ?controll|esp|arduino|pcb|sensor|robot/i, CHIP],
  [/comp.*sci|software|programm|\bcode\b|algorithm|data ?struct|web|dev/i, CODE],
  [/engineer|mechanic|\bcad\b|3d|model|manufactur|design/i, GEAR],
  [/math|physics|chem|science|calcul/i, ATOM],
  [/\bai\b|\bml\b|machine ?learn|neural|model|llm|intellig/i, BRAIN],
  [/financ|trad|market|stock|invest|money|econ/i, CHART],
  [/health|medic|bio|fitness|body/i, PULSE],
  [/book|read|literat|philosoph|history|note|writ/i, BOOK],
  [/inbox|misc|general|scratch|unsorted/i, INBOX],
  [/world|geo|travel|news|country/i, GLOBE],
];

export function domainIconDef(name: string): IconDef {
  for (const [re, def] of DOMAIN_RULES) if (re.test(name)) return def;
  return FOLDER;
}

// ── source kind → icon (a note's origin) ────────────────────────────────────
const DOC: IconDef = { key: "doc", paths: (<>
  <path d="M6 3h8l4 4v14H6z" /><path d="M14 3v4h4" /><path d="M9 12h6M9 16h6" />
</>) };
const LINK: IconDef = { key: "link", paths: (<>
  <path d="M9 15l6-6" /><path d="M11 6l1-1a3.5 3.5 0 0 1 5 5l-1 1" /><path d="M13 18l-1 1a3.5 3.5 0 0 1-5-5l1-1" />
</>) };
const IMAGE: IconDef = { key: "image", paths: (<>
  <rect x="4" y="5" width="16" height="14" rx="1.5" /><circle cx="9" cy="10" r="1.5" /><path d="M5 17l4-4 3 3 3-3 4 4" />
</>) };
const AUDIO: IconDef = { key: "audio", paths: (<>
  <path d="M3 12h3l3-6v12l3-6h3" /><path d="M18 9a4 4 0 0 1 0 6" />
</>) };
const REPORT: IconDef = { key: "report", paths: (<>
  <rect x="5" y="4" width="14" height="17" rx="1.5" /><path d="M9 4V3h6v1" /><path d="M9 10h6M9 14h4" />
</>) };
const NOTE: IconDef = { key: "note", paths: (<>
  <path d="M5 4h14v11l-4 4H5z" /><path d="M19 15h-4v4" />
</>) };

const SOURCE_ICONS: Record<string, IconDef> = {
  file: DOC, url: LINK, image: IMAGE, audio: AUDIO, report: REPORT, note: NOTE, text: NOTE,
};

export function sourceIconDef(kind: string): IconDef {
  return SOURCE_ICONS[kind] || NOTE;
}

// ── domain → hue (stable, on-theme palette) ─────────────────────────────────
// Void-violet-friendly spread; assigned by hashing the top-level domain so a
// domain keeps its color across renders.
const PALETTE = [
  "#8b7cf6", "#5eead4", "#60a5fa", "#f0abfc",
  "#fbbf24", "#4ade80", "#fb7185", "#818cf8", "#38bdf8", "#c084fc",
];

export function domainHue(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

// ── render helpers ──────────────────────────────────────────────────────────

/** Inline icon for the tree rows (DOM SVG). */
export function DomainIcon({ name, size = 15 }: { name: string; size?: number }) {
  const def = domainIconDef(name);
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
         strokeLinejoin="round" className="vault-ic">{def.paths}</svg>
  );
}

export function SourceIcon({ kind, size = 14 }: { kind: string; size?: number }) {
  const def = sourceIconDef(kind);
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
         strokeLinejoin="round" className="vault-ic">{def.paths}</svg>
  );
}

// ── toolbar tab icons (the buttons beside the search bar) ────────────────────
const TAB_PATHS: Record<string, ReactNode> = {
  graph: (<><circle cx="5" cy="6" r="2" /><circle cx="18.5" cy="7.5" r="2" /><circle cx="12" cy="17.5" r="2" /><path d="M6.7 7.2l4 8.5M16.8 9l-3.8 6.6M7 6.4l9.5 1" /></>),
  tree: (<><path d="M4 5v13" /><path d="M4 7h6M8 12h8M12 17h6" /></>),
  search: (<><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.3-4.3" /></>),
  plus: (<path d="M12 5v14M5 12h14" />),
  refresh: (<path d="M20.5 11a8 8 0 1 0-.7 4.2M20.5 5v6h-6" />),
};

export function TabIcon({ name, size = 15 }: { name: keyof typeof TAB_PATHS; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
         strokeLinejoin="round" className="vault-ic">{TAB_PATHS[name]}</svg>
  );
}
