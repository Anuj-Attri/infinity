// Shared markdown renderer — used in chat (ChatTile), SubAgent + Mission
// outputs. Extracted so style + syntax-highlighting choices stay consistent.
//
// Tile-link primitive: links with a `tile:<id>` href focus that tile on the
// canvas; plain http(s) links open as a Browser tile (everything is a tile) and
// focus it. Tile-name mentions + RAG citations are linkified by linkifyChat
// before render (gated on the live active-tile set, so there are no dead links).

import { useCallback, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import CodeBlock from "./CodeBlock";
import { useTiles } from "../state/TilesContext";
import { useBrowser } from "../state/BrowserContext";
import { useFiles } from "../state/FilesContext";
import { linkifyChat } from "./chatLinks";
import { artifactTileIdForSlug } from "../state/artifactStore";
import { copyWithToast } from "../native/actions";

type Props = {
  text: string;
  className?: string;
};

// Slash-ref slug → CANVAS tile id where they diverge (the chat-bar / refs use
// tileRegistry ids; the canvas uses TILE_REGISTRY ids). Inverse of the read
// path's canvas→slash ALIAS.
const SLASH_TO_TILE: Record<string, string> = {
  portfolio: "stocks", markets: "stocks", stocks: "stocks",
  tasks: "todo", todo: "todo", task: "todo",
};

export default function MarkdownView({ text, className = "md" }: Props) {
  const { registry, isTileActive, focusTile } = useTiles();
  const { openInBrowser } = useBrowser();
  const { files } = useFiles();

  // Resolve a slash-ref to a canvas tile id: known aliases, then a registry
  // tile whose id matches, then an attached file's slug → its tile id.
  const resolveSlug = useCallback((slug: string): string | null => {
    if (SLASH_TO_TILE[slug]) return SLASH_TO_TILE[slug];
    if (registry.some((t) => t.id === slug)) return slug;
    const f = files.find((x) => x.slug.toLowerCase() === slug);
    if (f) return f.id;
    // Generated artifact slugs (/red-barn) → their canvas tile id.
    return artifactTileIdForSlug(slug) ?? null;
  }, [registry, files]);

  // Linkify recomputes when the text or the active-tile set changes (the latter
  // via isTileActive's identity flipping on publishActiveIds).
  const rendered = useMemo(
    () => linkifyChat(text, registry, isTileActive, resolveSlug),
    [text, registry, isTileActive, resolveSlug],
  );

  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // react-markdown's default urlTransform STRIPS unknown schemes, which
        // nukes our `tile:` hrefs to "" — then the link falls through to a bare
        // <a href=""> that navigates/reloads the SPA (session reset). Preserve
        // tile: + ordinary safe schemes; drop only dangerous ones.
        urlTransform={(url: string) =>
          /^(tile:|https?:|mailto:|tel:|#)/i.test(url) ? url : ""
        }
        components={{
          code({ inline, className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || "");
            const value = String(children ?? "");
            if (inline) {
              return <code className={className} {...props}>{children}</code>;
            }
            return <CodeBlock language={match ? match[1] : ""} value={value} />;
          },
          a({ href, children, ...props }: any) {
            const h = String(href ?? "");

            // Tile focus link: tile:<id>[#cite]
            if (h.startsWith("tile:")) {
              const rest = h.slice("tile:".length);
              const isCite = rest.includes("#cite");
              const id = rest.split("#")[0];
              // No dead links: render plain text if the tile isn't on canvas.
              if (!id || !isTileActive(id)) {
                return <span>{children}</span>;
              }
              return (
                <button
                  type="button"
                  className={isCite ? "chat-citelink" : "chat-tilelink"}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); focusTile(id); }}
                  title="Focus this tile"
                >
                  {children}
                </button>
              );
            }

            // Web URL. Desktop: open as a Browser tile (everything is a tile).
            // Phone: the BrowserTile kind is hidden there, so route to the
            // system browser instead; long-press copies the URL.
            if (/^https?:\/\//i.test(h)) {
              return (
                <button
                  type="button"
                  className="chat-tilelink"
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    const id = openInBrowser(h);
                    // Let the new tile mount + lay out before panning to it.
                    window.setTimeout(() => focusTile(id), 120);
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    void copyWithToast(h, "Link copied");
                  }}
                  title={h}
                >
                  {children}
                </button>
              );
            }

            // Fallback: NEVER let an anchor navigate/reset the SPA. Empty,
            // relative, or in-page hrefs (the reset culprits) render as plain
            // text; real external schemes (mailto/tel) open out, never in-app.
            if (!h || h.startsWith("#") || h.startsWith("/")) {
              return <span>{children}</span>;
            }
            return <a href={h} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>;
          },
        }}
      >
        {rendered}
      </ReactMarkdown>
    </div>
  );
}
