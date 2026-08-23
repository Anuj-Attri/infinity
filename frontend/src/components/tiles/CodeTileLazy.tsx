// The Code tile, loaded on demand.
//
// Monaco is bundled rather than fetched from a CDN, which is the right trade —
// but it is roughly 4 MB, and CodeTile was a STATIC import from TileGrid. That
// would have put the entire editor engine in the initial page load for every
// user, including the ones who never open it.
//
// vite.config.ts already anticipated this:
//   "This only helps if the code also loads them lazily — a static import still
//    blocks first paint no matter which chunk it lands in. Wiring React.lazy
//    around the editor is the other half."
//
// This is that other half. TileGrid imports THIS module, so both of its call
// sites (the singleton tile and the multi-instance kind) get the same treatment
// from one place.
//
// WHY THE ERROR BOUNDARY. Lazy loading moves a failure mode from build time to
// run time: the chunk fetch can fail. Two realistic ways —
//   * a dropped or throttled connection, and
//   * a tab left open across a deploy, whose content-hashed chunk no longer
//     exists on the server (StaticFiles answers 404, not the new file).
// The app mounts exactly one ErrorBoundary, around the whole TileGrid, so
// without a local one a failed import would replace EVERY tile on the canvas
// with a stack trace. Before lazy loading, the worst case was one dead pane.

import { lazy, Suspense, useMemo, useState, type ComponentType } from "react";
import ErrorBoundary from "../ErrorBoundary";
import TileGlyph from "../TileGlyph";

function shell(meta: string, body: React.ReactNode) {
  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Code</span>
        <span className="tile-title-meta">{meta}</span>
      </div>
      <div className="tile-body tcode-body">{body}</div>
    </div>
  );
}

export default function CodeTileLazy() {
  // Bumped to retry. The import specifier is deliberately a STATIC string so
  // Vite still analyses it and emits the chunk — what changes per attempt is
  // the lazy object, not the path.
  //
  // A fresh `lazy()` is required, not just a remount: React memoises the
  // rejected payload on the lazy object itself, so re-rendering the same one
  // re-throws the cached error without touching the network. Keying a remount
  // onto the same lazy object looks like a retry and never is.
  const [attempt, setAttempt] = useState(0);
  const CodeTile = useMemo<ComponentType>(
    () => lazy(() => import("./CodeTile")),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [attempt],
  );

  return (
    <ErrorBoundary
      label="Code tile"
      // Remounting the boundary's subtree via `reset` is not enough on its own
      // — see above — so the retry does both: new lazy object, cleared error.
      fallback={(err, reset) => shell("unavailable", (
        <div className="tcode-noopen">
          <div className="tcode-noopen-h">The editor didn&apos;t load</div>
          <div className="tcode-noopen-sub">
            {/* Named plainly. "Something went wrong" would hide the one detail
                that tells the two causes apart. */}
            {/failed to fetch dynamically imported module|importing a module script|Load failed/i.test(err.message)
              ? "Its code could not be fetched. If the app was updated while this tab was open, reloading is the fix."
              : err.message.slice(0, 140)}
          </div>
          <div className="tcode-noopen-actions">
            <button type="button" className="tcode-btn"
                    onClick={() => { setAttempt((n) => n + 1); reset(); }}>
              Try again
            </button>
            <button type="button" className="tcode-linkbtn"
                    onClick={() => window.location.reload()}>
              reload the app
            </button>
          </div>
        </div>
      ))}
    >
      {/* The chunk-fetch phase. Wears the tile shell so opening the tile paints
          a real tile immediately rather than a hole that fills in later. */}
      <Suspense fallback={shell("loading…", <div className="tcode-loading">Loading editor…</div>)}>
        <CodeTile />
      </Suspense>
    </ErrorBoundary>
  );
}
