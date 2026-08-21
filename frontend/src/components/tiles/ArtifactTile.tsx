// Artifact tile — a generated media artifact from /image or /audio.
// ONE renderer, keyed on kind (image → <img>, audio → <audio>).
// Multi-instance: every generation spawns its own slugged, keepable
// tile. Void-violet frame, terse title.
//
// DISPATCH LIFECYCLE (mirrors SubAgent): independent of React. The job is
// registered + dispatched from runGenCommand in App.tsx (module-level, before
// the tile mounts), so a StrictMode unmount/remount never re-fires or aborts
// it. The component subscribes to its tileId's state via a listener map.

import { useEffect, useState } from "react";
import { postGenerate, postAnimate, fetchAnimateBlob, fetchAnimatePlayableUrl } from "../../api";
import { useTileId } from "../../state/TilesContext";
import { setArtifactData } from "../../state/artifactStore";
import { GEN_COMMANDS, type GenKind } from "../../state/genCommands";
import TileGlyph from "../TileGlyph";

export type ArtifactJob = {
  kind: GenKind;
  prompt: string;
  source?: string;   // data URL of the referenced source (X-to-X)
  slug?: string;     // /-reference name
  edited?: boolean;  // a referenced source was used
  result?: string;   // PRE-RESOLVED data URL — set by the chain engine, which
                     // awaits the dispatch itself; the tile then just displays
                     // it (no re-dispatch). Single /command runs leave this unset.
};

type RunState = {
  status: "running" | "done" | "error";
  dataUrl: string;   // result data URL (video: an object URL), "" until done
  text: string;      // optional model description
  stage?: string;    // streamed progress line (video: authoring/rendering/encoding)
};

// ── Module-level state — survives React StrictMode remounts ──────────────────
const _JOBS    = new Map<string, ArtifactJob>();
const _STATE   = new Map<string, RunState>();
const _STARTED = new Set<string>();
const _SUBS    = new Map<string, Set<() => void>>();

export function registerArtifactJob(tileId: string, job: ArtifactJob): void {
  _JOBS.set(tileId, job);
}

function notify(tileId: string): void {
  _SUBS.get(tileId)?.forEach((cb) => { try { cb(); } catch { /* ignore */ } });
}
function subscribe(tileId: string, cb: () => void): () => void {
  if (!_SUBS.has(tileId)) _SUBS.set(tileId, new Set());
  _SUBS.get(tileId)!.add(cb);
  return () => _SUBS.get(tileId)?.delete(cb);
}

// Called by runGenCommand before spawnTileWithId. Idempotent per tileId.
export function startArtifactDispatch(tileId: string): void {
  if (_STARTED.has(tileId)) return;
  const job = _JOBS.get(tileId);
  if (!job) return;

  _STARTED.add(tileId);

  // Pre-resolved by the chain engine (it awaited the dispatch already): just
  // display, no second dispatch.
  if (job.result) {
    if (job.kind === "video") {
      // Stored result is the backend PATH — a <video> can't auth it directly;
      // mint a fresh ticketed URL (same lane as a live render's finish).
      _STATE.set(tileId, { status: "running", dataUrl: "", text: "", stage: "loading…" });
      notify(tileId);
      const path = job.result;
      void fetchAnimatePlayableUrl(path)
        .then((abs) => {
          const s = _STATE.get(tileId);
          if (s) { s.status = "done"; s.dataUrl = abs; s.stage = undefined; notify(tileId); }
        })
        .catch(() => {
          const s = _STATE.get(tileId);
          if (s) { s.status = "error"; notify(tileId); }
        });
      return;
    }
    _STATE.set(tileId, { status: "done", dataUrl: job.result, text: "" });
    notify(tileId);
    return;
  }

  // Video is the streamed local-render lane, never the blocking postGenerate.
  if (job.kind === "video") {
    _STARTED.delete(tileId);           // hand the guard to the streamed path
    startAnimateDispatch(tileId);
    return;
  }

  _STATE.set(tileId, { status: "running", dataUrl: "", text: "" });
  const cfg = GEN_COMMANDS[job.kind];

  postGenerate(job.kind, job.prompt, job.source, cfg.unavailable)
    .then((res) => {
      const s = _STATE.get(tileId);
      if (s) { s.status = "done"; s.dataUrl = res.data; s.text = res.text; notify(tileId); }
      // Store bytes on the artifact so /<slug> can be referenced/edited/summarized.
      setArtifactData(tileId, res.data);
    })
    .catch(() => {
      const s = _STATE.get(tileId);
      if (s) { s.status = "error"; notify(tileId); }   // opaque — message comes from cfg
    });
}

// ── /animate — streamed local Hyperframes render (video kind) ────────────────
// Same module-level lifecycle as startArtifactDispatch, but the dispatch is an
// NDJSON stage stream (authoring → rendering N/M → encoding) and the result is
// an MP4 served by the backend: fetched as an authed blob → object URL for the
// <video>. Called by runAnimate in App.tsx before the tile mounts.
export function startAnimateDispatch(tileId: string): void {
  if (_STARTED.has(tileId)) return;
  const job = _JOBS.get(tileId);
  if (!job) return;
  _STARTED.add(tileId);
  _STATE.set(tileId, { status: "running", dataUrl: "", text: "", stage: "authoring…" });
  notify(tileId);

  void postAnimate(job.prompt, {
    onStage: (stage, frames, total) => {
      const s = _STATE.get(tileId);
      if (!s) return;
      s.stage = stage === "rendering" && total
        ? `rendering ${frames ?? 0}/${total} frames`
        : `${stage}…`;
      notify(tileId);
    },
    onDone: (url) => {
      // Ticketed plain-http URL first: Android WebView's media player streams
      // it natively (Range requests) where blob object-URLs are flaky. Blob →
      // object URL stays as the fallback if the ticket mint fails.
      void fetchAnimatePlayableUrl(url)
        .then((abs) => {
          const s = _STATE.get(tileId);
          if (!s) return;
          s.status = "done";
          s.dataUrl = abs;
          s.stage = undefined;
          notify(tileId);
          // The artifact remembers the BACKEND path (small, refetchable) —
          // the playable URL is session-local display state.
          setArtifactData(tileId, url);
        })
        .catch(() => fetchAnimateBlob(url)
          .then((blob) => {
            const s = _STATE.get(tileId);
            if (!s) return;
            s.status = "done";
            s.dataUrl = URL.createObjectURL(blob);
            s.stage = undefined;
            notify(tileId);
            setArtifactData(tileId, url);
          })
          .catch(() => {
            const s = _STATE.get(tileId);
            if (s) { s.status = "error"; notify(tileId); }
          }));
    },
    onError: () => {
      const s = _STATE.get(tileId);
      if (s) { s.status = "error"; notify(tileId); }
    },
  // notify_route: tapping the "render ready" notification deep-dives THIS tile.
  }, `tile:${tileId}`);
}

// ── Component ────────────────────────────────────────────────────────────────

export default function ArtifactTile() {
  const tileId = useTileId();
  const [, setTick] = useState(0);

  useEffect(() => {
    startArtifactDispatch(tileId);
    return subscribe(tileId, () => setTick((n) => n + 1));
  }, [tileId]);

  const job   = _JOBS.get(tileId);
  const kind  = job?.kind ?? "image";
  const cfg   = GEN_COMMANDS[kind];
  const state = _STATE.get(tileId) ?? { status: "running" as const, dataUrl: "", text: "" };
  const promptLabel = (job?.prompt ?? "").slice(0, 80) + ((job?.prompt ?? "").length > 80 ? "…" : "");

  // Per-kind progress copy (honest present-continuous; video shows real stages).
  const pending = kind === "video" ? (state.stage ?? "rendering…")
    : kind === "audio" ? "composing…" : "generating…";

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />{job?.edited ? "Edit" : cfg.label}</span>
        {job?.slug && <span className="tile-title-meta">/{job.slug}</span>}
        <span className={`timage-status timage-status-${state.status}`}>
          {state.status === "running" ? pending : state.status === "error" ? "failed" : ""}
        </span>
      </div>
      <div
        className="tile-body timage-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {state.status === "running" ? (
          <div className="timage-pending">{pending}</div>
        ) : state.status === "error" ? (
          <div className="timage-error">{cfg.unavailable}</div>
        ) : (
          <>
            {kind === "image" && <img className="timage-img" src={state.dataUrl} alt={promptLabel || "Generated image"} draggable={false} />}
            {kind === "audio" && <audio className="tartifact-audio" src={state.dataUrl} controls />}
            {kind === "video" && <video className="tartifact-video" src={state.dataUrl} controls playsInline loop />}
            {promptLabel && <div className="timage-prompt">{promptLabel}</div>}
          </>
        )}
      </div>
    </div>
  );
}
