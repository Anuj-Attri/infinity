// User-attached files, rendered as tiles on the whiteboard.
//
// "Attach image / doc" in the TopToolbar opens an OS file picker, the
// file is read into memory (image -> dataURL, text -> string content),
// and a new entry is added here. Each entry also gets REGISTERED in
// tileRegistry so the file is /-referenceable from the chat bubble like
// any built-in tile (`/myfile` -> silent context injection on the next
// turn). This is the one and only path attachments take to Nova — there
// is no separate "attachment bubble chip" anymore.
//
// Honest limitation: this slash-injection path is TEXT-only. Image tiles inject
// filename + mime + size only; their bytes are NOT inlined here, and the provider
// line says so explicitly (use /image or a vision-capable model to view image
// contents). Doc/text tiles inject their full text content.

import {
  createContext, useCallback, useContext, useMemo, useRef, useState,
  type ReactNode,
} from "react";
import {
  registerExtraTile, unregisterExtraTile, type TileSpec,
} from "./tileRegistry";
import { extractFile } from "../api";

export type FileKind = "image" | "text" | "binary";

/** Backend-provided structured render payload — present for extracted
 *  documents (xlsx/pptx/pdf/docx). Nova never sees this; the FileTile
 *  uses it to render a richer view than flat text. */
export type FileView =
  | { type: "table";  sheets: Array<{ name: string; rows: Array<Array<string | number | boolean>> }> }
  | { type: "slides"; slides: Array<{ n: number; title?: string; lines?: string[]; tables?: string[][][]; notes?: string }> }
  | { type: "pages";  pages: Array<{ n: number; text: string }> }
  | { type: "doc";    blocks: Array<{ type: "heading"; level: number; text: string } | { type: "p"; text: string } | { type: "table"; rows: string[][] }> };

export type FileItem = {
  id: string;          // unique tile id, e.g. "file_<seq>"
  name: string;        // original filename
  slug: string;        // slash-safe id used for `/slug` references
  mime: string;
  size: number;
  kind: FileKind;
  text?: string;       // for kind === "text"
  dataUrl?: string;    // for kind === "image"
  view?: FileView;     // rich render payload for extracted docs
  blob?: Blob;         // original bytes — kept for doc rendering (PDF viewer)
                       // and RAG ingest-from-bytes (re-extract server-side)
  createdAt: number;
};

type Ctx = {
  files: FileItem[];
  /** Open the OS file picker and add the chosen file as a new tile.
   *  Resolves to the new FileItem, or null if the user cancelled. */
  openFile: () => Promise<FileItem | null>;
  /** Add an image from a data URL (e.g. a phone-camera capture) as an image
   *  tile — same path uploaded images take, so it flows into /summarize + /image. */
  addImageFromDataUrl: (name: string, dataUrl: string) => FileItem;
  closeFile: (id: string) => void;
  getById: (id: string) => FileItem | undefined;
};

const FilesCtx = createContext<Ctx | null>(null);

const MAX_TEXT_BYTES  = 250 * 1024;
const MAX_IMAGE_BYTES =  12 * 1024 * 1024;
const MAX_DOC_BYTES   =  20 * 1024 * 1024;   // mirrors backend /files/extract cap

// Backend handles these by extracting text server-side and returning it
// so the tile renders as a readable text tile rather than a binary card.
const EXTRACT_EXT_RE = /\.(pdf|docx|xlsx|xlsm|pptx)$/i;

const TEXT_MIME_PREFIXES = ["text/", "application/json", "application/xml", "application/javascript"];
const TEXT_EXT_RE = /\.(md|txt|csv|json|yaml|yml|toml|ts|tsx|js|jsx|py|rs|go|java|c|cc|cpp|h|hpp|css|html|sh|ps1|env|log)$/i;

function looksLikeImage(f: File): boolean { return f.type.startsWith("image/"); }
function looksLikeText(f: File): boolean {
  if (TEXT_MIME_PREFIXES.some((p) => f.type.startsWith(p))) return true;
  return TEXT_EXT_RE.test(f.name);
}

function looksExtractable(f: File): boolean {
  return EXTRACT_EXT_RE.test(f.name);
}

async function extractDocument(f: File): Promise<{ text: string; view?: FileView } | null> {
  // Authed extraction via apiFetch — /files/extract is behind require_auth, so
  // a token-less call 401s and the doc silently degrades to binary (the old bug).
  const r = await extractFile(f);
  return r ? { text: r.text, view: r.view as FileView | undefined } : null;
}

function readDataUrl(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(r.error);
    r.onload  = () => resolve(String(r.result));
    r.readAsDataURL(f);
  });
}

const RESERVED_SLUGS = new Set(["portfolio", "news", "clock", "tasks", "notes", "docs", "weather", "stocks", "usage", "summary", "image", "audio", "research", "chain", "plan"]);

function slugify(name: string, existing: Set<string>): string {
  const base = name.toLowerCase()
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_") || "file";
  let candidate = base;
  let n = 2;
  while (existing.has(candidate) || RESERVED_SLUGS.has(candidate)) {
    candidate = `${base}_${n}`;
    n++;
  }
  return candidate;
}

function buildTileSpec(item: FileItem): TileSpec {
  const description =
    item.kind === "image" ? `Attached image: ${item.name} (text-only model — image bytes not sent)`
    : item.kind === "text"  ? `Attached file: ${item.name}`
    : `Attached file: ${item.name} (binary)`;

  return {
    id: item.slug,
    label: `/${item.slug}`,
    description,
    provide: async () => {
      const head = `[/${item.slug} — ${item.name}]`;
      if (item.kind === "image") {
        return [
          head,
          `  kind: image`,
          `  mime: ${item.mime}`,
          `  size: ${item.size} bytes`,
          `  note: image bytes are not inlined into this text context (use /image or a vision-capable model to view contents).`,
        ].join("\n");
      }
      if (item.kind === "text" && item.text != null) {
        return `${head}\n${item.text}`;
      }
      return [
        head,
        `  kind: binary`,
        `  mime: ${item.mime}`,
        `  size: ${item.size} bytes`,
        `  note: binary content not inlined; only filename is available.`,
      ].join("\n");
    },
  };
}

export function FilesProvider({ children }: { children: ReactNode }) {
  const [files, setFiles] = useState<FileItem[]>([]);
  const seqRef = useRef(0);
  const filesRef = useRef<FileItem[]>([]);
  filesRef.current = files;

  const openFile = useCallback((): Promise<FileItem | null> => {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept =
        "image/*," +
        ".pdf,.docx,.xlsx,.xlsm,.pptx," +
        ".md,.txt,.csv,.json,.yaml,.yml,.toml," +
        ".ts,.tsx,.js,.jsx,.py,.rs,.go,.java,.c,.cc,.cpp,.h,.hpp," +
        ".css,.html,.sh,.ps1,.env,.log";
      input.onchange = async () => {
        const f = input.files?.[0];
        if (!f) { resolve(null); return; }
        seqRef.current += 1;
        const id = `file_${Date.now().toString(36)}_${seqRef.current}`;
        const existingSlugs = new Set(filesRef.current.map((x) => x.slug));
        const slug = slugify(f.name, existingSlugs);

        let kind: FileKind = "binary";
        let text: string | undefined;
        let dataUrl: string | undefined;
        let view: FileView | undefined;
        let blob: Blob | undefined;

        if (looksLikeImage(f) && f.size <= MAX_IMAGE_BYTES) {
          try { dataUrl = await readDataUrl(f); kind = "image"; }
          catch { /* leave binary */ }
        } else if (looksLikeText(f) && f.size <= MAX_TEXT_BYTES) {
          try { text = await f.text(); kind = "text"; }
          catch { /* leave binary */ }
        } else if (looksExtractable(f) && f.size <= MAX_DOC_BYTES) {
          // PDF / .docx / .xlsx / .pptx. Keep the raw bytes regardless of
          // upload-time extraction so the tile can render the real document
          // (PDF viewer) and RAG can ingest from the bytes (re-extract
          // server-side) even if this best-effort extraction returns nothing.
          blob = f;
          const extracted = await extractDocument(f);
          if (extracted) { text = extracted.text; view = extracted.view; kind = "text"; }
          // else: keep kind "binary" but we still have `blob` for render + ingest.
        }

        const item: FileItem = {
          id, name: f.name, slug,
          mime: f.type || "application/octet-stream",
          size: f.size, kind, text, dataUrl, view, blob,
          createdAt: Date.now(),
        };
        setFiles((cur) => [...cur, item]);
        registerExtraTile(buildTileSpec(item));
        resolve(item);
      };
      input.click();
    });
  }, []);

  // Camera path: a captured photo (data URL) becomes an image tile — identical
  // to an uploaded image, so /summarize /<slug> and /image /<slug> read its
  // dataUrl just the same. Mirrors the image branch of openFile.
  const addImageFromDataUrl = useCallback((name: string, dataUrl: string): FileItem => {
    seqRef.current += 1;
    const id = `file_${Date.now().toString(36)}_${seqRef.current}`;
    const existingSlugs = new Set(filesRef.current.map((x) => x.slug));
    const slug = slugify(name, existingSlugs);
    const mimeMatch = /^data:([^;]+);/.exec(dataUrl);
    const item: FileItem = {
      id, name, slug,
      mime: mimeMatch ? mimeMatch[1] : "image/jpeg",
      size: Math.round((dataUrl.length * 3) / 4),   // ~base64 byte estimate
      kind: "image", dataUrl,
      createdAt: Date.now(),
    };
    setFiles((cur) => [...cur, item]);
    registerExtraTile(buildTileSpec(item));
    return item;
  }, []);

  const closeFile = useCallback((id: string) => {
    setFiles((cur) => {
      const target = cur.find((x) => x.id === id);
      if (target) unregisterExtraTile(target.slug);
      return cur.filter((x) => x.id !== id);
    });
  }, []);

  const getById = useCallback((id: string) => filesRef.current.find((f) => f.id === id), []);

  const value = useMemo<Ctx>(() => ({ files, openFile, addImageFromDataUrl, closeFile, getById }),
    [files, openFile, addImageFromDataUrl, closeFile, getById]);

  return <FilesCtx.Provider value={value}>{children}</FilesCtx.Provider>;
}

export function useFiles(): Ctx {
  const v = useContext(FilesCtx);
  if (!v) throw new Error("useFiles called outside FilesProvider");
  return v;
}
