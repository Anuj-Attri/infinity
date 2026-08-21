// Generative-command config — the shared abstraction behind /image and
// /audio. Each modality is ONE row: {command, kind, label, verb, unavailable}.
// The command flow (parse → resolve source bytes → dispatch → slugged artifact
// tile → chat hyperlink) is identical across all three (App.runGenCommand);
// only this config + the tile renderer (ArtifactTile, keyed on kind) differ.
// Adding /music, /speech, etc. later = one row here + (if new) a renderer case.

// (Photoreal video stays parked — OpenRouter hosts no video-output models and
// the local ComfyUI lane is gone, 2026-07-03. "video" here is /animate:
// code-driven MOTION GRAPHICS rendered LOCALLY via HeyGen Hyperframes — an
// LLM authors an HTML composition, headless Chrome + FFmpeg render it to a
// deterministic MP4 on this box. $0 per render. Streamed via runAnimate /
// startAnimateDispatch, NOT the blocking postGenerate path.)
export type GenKind = "image" | "audio" | "video";

export type GenConfig = {
  kind: GenKind;
  command: string;       // slash keyword, e.g. "image"
  label: string;         // tile title / chat noun, e.g. "Image"
  /** Present-continuous verb for the chat line (honest: it's still generating). */
  verb: string;          // "Generating" | "Composing" | "Rendering"
  editVerb: string;      // when a source is referenced: "Editing" | "Remixing" | "Animating"
  unavailable: string;   // opaque failure string
  /** False = no viable backend model yet; the command stays discoverable but
   *  cleanly states it's unavailable WITHOUT spawning a dead tile. */
  available: boolean;
  /** Source-media kinds this modality's model can actually INGEST for editing.
   *  Driven by the real model's input modalities — e.g. Lyria 3 takes text+IMAGE
   *  (so an image can seed music) but NOT audio (no audio-to-audio edit). A
   *  referenced source whose kind isn't here degrades honestly, never silently. */
  sourceKinds: GenKind[];
};

export const GEN_COMMANDS: Record<GenKind, GenConfig> = {
  // Nano Banana 2: image in+out → image-to-image edit supported.
  image: { kind: "image", command: "image", label: "Image", verb: "Generating", editVerb: "Editing",  unavailable: "Image unavailable.", available: true,  sourceKinds: ["image"] },
  // Lyria 3: input modalities text+image (NOT audio) → an image can seed a
  // track, but there is NO audio-to-audio edit. Audio source refs degrade honestly.
  audio: { kind: "audio", command: "audio", label: "Audio", verb: "Composing",  editVerb: "Scoring",  unavailable: "Audio unavailable.", available: true,  sourceKinds: ["image"] },
  // /animate — local Hyperframes motion-graphics MP4. No source-media editing.
  video: { kind: "video", command: "animate", label: "Animation", verb: "Rendering", editVerb: "Re-rendering", unavailable: "Animation unavailable.", available: true, sourceKinds: [] },
};

export const GEN_KINDS = Object.keys(GEN_COMMANDS) as GenKind[];
