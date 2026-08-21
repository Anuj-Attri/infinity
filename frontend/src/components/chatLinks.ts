// Chat → tile hyperlink primitive (text transform).
//
// Turns references in Nova's reply text into markdown links with a `tile:`
// href, which MarkdownView renders as clickable tile-focus links:
//   1. RAG citations like [MD-training-plan.pdf]  -> the Docs tile (tile:docs#cite)
//   2. SLASH references like /notes, /portfolio   -> that tile (how Nova actually
//      names tiles, matching the chat-bar / convention)
//   3. mentions of an ACTIVE tile's display name (Notes, Weather, …) -> that tile
//
// No dead links by construction: slash-refs + name mentions linkify only for
// tiles currently on the canvas, and citations only when the Docs tile exists.
// URLs are left alone — remark-gfm auto-links them and MarkdownView routes a
// click to a Browser tile.

export type RegistryTile = { id: string; label: string };

const FILE_EXT = "pdf|docx|xlsx|pptx|xlsm|txt|md|csv|json";
const CITE_RE = new RegExp(`\\[([^\\[\\]\\n]+\\.(?:${FILE_EXT}))\\]`, "gi");

// Slash reference like /notes or /md_training_plan. Excludes a preceding word
// char / ":" / "/" so it never fires inside "http://x", "src/notes", etc.
const SLASH_RE = /(^|[^\w:/])\/([a-zA-Z][\w-]*)/g;

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * @param text        Nova's reply markdown.
 * @param registry    TILE_REGISTRY entries (id + label).
 * @param isActive    true if a tile id is currently on the canvas.
 * @param resolveSlug maps a slash-ref slug (lowercased) to a CANVAS tile id, or
 *                    null if it doesn't correspond to a tile. Reuses the
 *                    slash→tile aliasing (e.g. portfolio→stocks, tasks→todo)
 *                    + file-tile slugs. Optional (display-name + citation
 *                    linking still work without it).
 */
export function linkifyChat(
  text: string,
  registry: RegistryTile[],
  isActive: (id: string) => boolean,
  resolveSlug?: (slug: string) => string | null,
): string {
  if (!text) return text;
  const docsActive = isActive("docs");
  const activeTiles = registry.filter((t) => isActive(t.id) && t.label);

  // Split out inline-code spans so we never linkify inside `code`.
  const parts = text.split(/(`[^`]*`)/);
  return parts
    .map((seg, i) => {
      if (i % 2 === 1) return seg; // code span — leave untouched
      let s = seg;

      // 1. Citations -> Docs tile (only if a Docs tile actually exists).
      if (docsActive) {
        s = s.replace(CITE_RE, (_m, fn) => `[${fn}](tile:docs#cite)`);
      }

      // 2. Slash references -> their tile. This is how Nova actually refers to
      //    tiles ("/notes"). Case-insensitive; resolved to a canvas tile id;
      //    only linkified when that tile is active (no dead links).
      if (resolveSlug) {
        s = s.replace(SLASH_RE, (m, pre, slug) => {
          const id = resolveSlug(String(slug).toLowerCase());
          if (!id || !isActive(id)) return m;          // unknown / not open -> plain
          return `${pre}[/${slug}](tile:${id})`;
        });
      }

      // 3. Display-name mentions -> their tile. Case-SENSITIVE on the proper
      //    label (Nova capitalizes tile names) + first occurrence each, to keep
      //    false positives on common words low. Skips matches already sitting
      //    inside a markdown link or a slash-ref.
      for (const t of activeTiles) {
        const re = new RegExp(`(^|[^\\[\\w/])(${escapeRe(t.label)})(?![\\w\\]])`);
        let done = false;
        s = s.replace(re, (m, pre, lbl) => {
          if (done) return m;
          done = true;
          return `${pre}[${lbl}](tile:${t.id})`;
        });
      }
      return s;
    })
    .join("");
}
