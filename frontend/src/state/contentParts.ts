// Gather-by-kind content-part assembler — the multimodal foundation for the
// generative suite. Maps a source's KIND (text/image/audio/file) to the
// right OpenRouter content part(s). /summarize uses this so the omni-model
// (gemini-3.1-flash-lite) sees REAL bytes (image/audio/PDF), not text
// metadata. Adding a modality later = ONE case here, not a rewrite.
//
// Per-modality OpenRouter content-part format:
//   text   → { type:"text",  text }
//   image  → { type:"image_url",   image_url:{ url:<data-url> } }
//   audio  → { type:"input_audio", input_audio:{ data:<base64>, format } }
//   video  → { type:"file",   file:{ filename, file_data:<data-url> } }   (Gemini reads inline file data)
//   file   → { type:"file",   file:{ filename, file_data:<data-url> } }   (e.g. raw PDF bytes)

import type { ContentPart } from "../api";

export type SourceKind = "text" | "image" | "audio" | "file";

function parseDataUrl(u: string): { mime: string; base64: string } | null {
  const m = /^data:([^;]+);base64,(.*)$/s.exec(u);
  return m ? { mime: m[1], base64: m[2] } : null;
}

function audioFormat(mime: string): string {
  const m = mime.toLowerCase();
  if (m.includes("wav")) return "wav";
  if (m.includes("ogg")) return "ogg";
  if (m.includes("flac")) return "flac";
  if (m.includes("aac")) return "aac";
  return "mp3";   // mpeg/mp3/default
}

export type SourceSpec = {
  kind: SourceKind;
  label: string;        // small text anchor, e.g. "[/uav — image]"
  dataUrl?: string;     // bytes for image/audio/file (data URL)
  text?: string;        // text for text sources
  filename?: string;    // for file parts
};

/** Turn one gathered source into OpenRouter content part(s). Unreadable bytes
 *  degrade to a text "unavailable" marker — never fabricated, mirroring the
 *  text path's honesty. */
export function partsForSource(s: SourceSpec): ContentPart[] {
  const label: ContentPart = { type: "text", text: s.label };
  switch (s.kind) {
    case "image": {
      if (!s.dataUrl) return [{ type: "text", text: `${s.label} (image bytes unavailable)` }];
      return [label, { type: "image_url", image_url: { url: s.dataUrl } }];
    }
    case "audio": {
      const p = s.dataUrl ? parseDataUrl(s.dataUrl) : null;
      if (!p) return [{ type: "text", text: `${s.label} (audio bytes unavailable)` }];
      return [label, { type: "input_audio", input_audio: { data: p.base64, format: audioFormat(p.mime) } }];
    }
    case "file": {
      if (!s.dataUrl) return [{ type: "text", text: s.text || `${s.label} (unavailable)` }];
      return [label, { type: "file", file: { filename: s.filename || "file", file_data: s.dataUrl } }];
    }
    case "text":
    default:
      return [{ type: "text", text: s.text || `${s.label} (unavailable)` }];
  }
}
