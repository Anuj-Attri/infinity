// Artifact registry — makes GENERATED tiles (/image, /audio)
// /-referenceable, the same way uploaded file tiles are. An artifact gets a
// concise kebab-case slug derived from its prompt; the slug is registered as a
// slash-tile (tileRegistry EXTRA_TILES) so it autocompletes + resolves like a
// file, and its bytes (a data URL) are stored so /image can read them back for
// iterative editing (image-to-image). Generalizes across artifact kinds.

import { registerExtraTile, getAllTiles } from "./tileRegistry";

export type ArtifactKind = "image" | "audio" | "video" | "plan";

export type Artifact = {
  slug: string;        // /-reference, kebab-case, unique across all tiles
  tileId: string;      // canvas tile id (e.g. "image_abc")
  kind: ArtifactKind;
  name: string;        // display name (== slug today)
  prompt: string;      // the generating prompt/instruction (or goal, for a plan)
  dataUrl: string;     // the bytes; "" until generation completes (media kinds)
  text?: string;       // TEXT artifacts (a plan): the markdown, so /<slug> in
                       // normal chat injects the real content for Nemo to read.
};

const _bySlug   = new Map<string, Artifact>();
const _byTileId = new Map<string, Artifact>();

// Dropped when slugging a prompt — articles/prepositions/filler + common edit
// verbs, so "a red barn" → "red-barn" and "make it brighter" → "brighter".
const STOPWORDS = new Set([
  "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "but",
  "with", "this", "that", "these", "those", "into", "as", "by", "from", "is",
  "it", "its", "my", "your", "make", "made", "turn", "give", "add", "set",
  "please", "image", "picture", "photo", "render", "generate", "create", "draw",
]);

/** A concise, safe, UNIQUE kebab-case slug derived from a prompt. Unique across
 *  every registered tile (artifacts + files + built-ins), so two similar prompts
 *  never collide ("red-barn", "red-barn-2", …). */
export function makeArtifactSlug(prompt: string): string {
  const words = prompt
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w && !STOPWORDS.has(w));
  let base = words.slice(0, 4).join("-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  if (!base) base = "image";

  const taken = new Set(getAllTiles().map((t) => t.id));
  for (const a of _bySlug.keys()) taken.add(a);

  let slug = base;
  let n = 2;
  while (taken.has(slug)) { slug = `${base}-${n}`; n += 1; }
  return slug;
}

/** Register an artifact (bytes may arrive later via setArtifactData). Also
 *  registers the slug as a slash-tile so it autocompletes + /-resolves. */
export function registerArtifact(art: Artifact): void {
  _bySlug.set(art.slug, art);
  _byTileId.set(art.tileId, art);
  registerExtraTile({
    id: art.slug,
    label: `/${art.slug}`,
    description: art.kind === "plan" ? "Plan" : `Generated ${art.kind}`,
    // Normal-chat slash-injection. A PLAN is text → inject the full plan so
    // Nemo can read it (THE BRIDGE: user references the plan → Nemo turns it
    // into a Claude-Code prompt). Media artifacts only announce themselves (the
    // local text model can't see bytes; /image reads them directly).
    provide: async () => {
      const a = _bySlug.get(art.slug);
      if (a?.kind === "plan") {
        return `[/${a.slug} — plan]\n${a.text || "(plan still being written)"}`;
      }
      return `[/${art.slug} — generated ${art.kind}: "${art.prompt}"]\n` +
        `  note: visual artifact; reference it in /image to edit. The image bytes ` +
        `go to the image model, not the text model.`;
    },
  });
}

/** Fill in the bytes once generation completes (keyed by canvas tile id). */
export function setArtifactData(tileId: string, dataUrl: string): void {
  const a = _byTileId.get(tileId);
  if (a) a.dataUrl = dataUrl;
}

/** Fill in a TEXT artifact's content (a plan), keyed by canvas tile id. */
export function setArtifactText(tileId: string, text: string): void {
  const a = _byTileId.get(tileId);
  if (a) a.text = text;
}

export function getArtifactBySlug(slug: string): Artifact | undefined {
  return _bySlug.get(slug.toLowerCase());
}

/** slug → canvas tile id, for resolving `/slug` chat mentions to tile: links. */
export function artifactTileIdForSlug(slug: string): string | undefined {
  return _bySlug.get(slug.toLowerCase())?.tileId;
}
