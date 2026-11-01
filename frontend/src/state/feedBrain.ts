// Quick-add to the Vault — ONE path that files text, a pasted link, or a file
// into the unified knowledge base. A link is fetched, extracted and sanitized
// SERVER-side (browse.py), and the source URL is kept as the citation, because
// an answer without a source is a bug.
//
// Fail-closed: this throws rather than reporting a success it did not achieve.

import { vaultIngest, vaultIngestFile } from "../api";

const URL_RE = /^https?:\/\/\S+$/i;

export type FeedResult = { source: string; chunks: number; already: boolean };

/** Feed plain text OR a pasted link. A link is fetched + extracted server-side. */
export async function feedTextOrLink(input: string): Promise<FeedResult> {
  const s = (input || "").trim();
  if (!s) throw new Error("Nothing to add.");
  const r = URL_RE.test(s)
    ? await vaultIngest({ url: s })
    : await vaultIngest({ text: s, title: s.slice(0, 48) || "note" });
  return { source: r.node.title || r.node.slug, chunks: 1, already: false };
}

/** Feed a file's bytes (the backend runs the real extractor + OCR). */
export async function feedFile(blob: Blob, name: string): Promise<FeedResult> {
  const r = await vaultIngestFile(blob, name || "document");
  return { source: r.node.title || r.node.slug, chunks: 1, already: false };
}
