// Monaco, served from our own origin.
//
// `@monaco-editor/react` defaults to fetching the entire editor engine from
// `cdn.jsdelivr.net` at runtime — it does this whenever `loader.config()` has
// not been called, and it had never been called. The shipped bundle carried the
// literal string `jsdelivr.net/npm/monaco-editor@0.55.1/min/vs`, so:
//
//   * the editor did not work offline, behind a strict CSP, or anywhere
//     jsdelivr is blocked — and it fails by hanging on a blank pane, with no
//     timeout and no error, which is indistinguishable from "the app is broken";
//   * a third party could serve arbitrary script into a product whose whole
//     pitch is that your files and your company's documents are safe in it;
//   * the version was pinned by the LOADER (0.55.1) and had already drifted from
//     the copy in node_modules (0.56.0), so what we tested was not what ran.
//
// Importing monaco here and handing the instance to `loader.config({ monaco })`
// removes the network from the path entirely: the engine is part of our bundle,
// versioned in package.json, and served from the same origin as everything else.
//
// `monaco-editor` was also an UNDECLARED dependency — present in node_modules
// only because npm auto-installs peers. It is a real dependency now. This
// project has been bitten by exactly that before (beautifulsoup4 and lxml,
// imported by browse.py and absent from requirements.txt).
//
// WORKERS. Monaco does syntax, IntelliSense and validation in web workers. Get
// this wrong and the editor still renders — it just silently loses everything
// that makes it an editor, or floods the console with worker errors. Vite's
// `?worker` suffix compiles each one to a real same-origin worker bundle and
// hands back a constructor, which is what `MonacoEnvironment.getWorker` wants.
//
// This module is imported for its SIDE EFFECTS and must run before the first
// `<Editor>` mounts. CodeTile imports it at module scope, and CodeTile itself is
// lazily loaded, so none of this weight is in the initial page.

import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";

// NOTE the specifiers. monaco-editor's package `exports` maps "./*" to
// "./esm/vs/*.js", so the conventional-looking
// `monaco-editor/esm/vs/editor/editor.worker` resolves to
// `esm/vs/esm/vs/editor/editor.worker.js` and the build fails to resolve it.
// The path is written relative to esm/vs, not to the package root.
import editorWorker from "monaco-editor/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/language/json/json.worker?worker";
import cssWorker from "monaco-editor/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/language/html/html.worker?worker";
import tsWorker from "monaco-editor/language/typescript/ts.worker?worker";

declare global {
  interface Window {
    MonacoEnvironment?: monaco.Environment;
  }
}

window.MonacoEnvironment = {
  getWorker(_moduleId: string, label: string) {
    switch (label) {
      case "json":
        return new jsonWorker();
      case "css":
      case "scss":
      case "less":
        return new cssWorker();
      case "html":
      case "handlebars":
      case "razor":
        return new htmlWorker();
      case "typescript":
      case "javascript":
        return new tsWorker();
      default:
        return new editorWorker();
    }
  },
};

// ── Theme ───────────────────────────────────────────────────────────────────
//
// Monaco's stock `vs-dark` is VS Code's #1e1e1e, which sits a shade off the
// app's own #1f1f1f surface and reads as a foreign panel dropped into the
// canvas. These two themes take their background and gutter colours from the
// same tokens as every other tile, so the editor looks like part of the app
// rather than an embed.
//
// Only surface colours are overridden. The syntax palettes are inherited from
// vs / vs-dark, because rewriting those is a large amount of taste for very
// little gain, and getting them subtly wrong is worse than using the defaults.

const LIGHT_BG = "#ffffff";   // --bg-tile, light
const DARK_BG = "#1f1f1f";    // --bg-tile, dark

monaco.editor.defineTheme("infinity-light", {
  base: "vs",
  inherit: true,
  rules: [],
  colors: {
    "editor.background": LIGHT_BG,
    "editorGutter.background": LIGHT_BG,
    "editor.lineHighlightBackground": "#00000008",
    "editorLineNumber.foreground": "#100e1459",
    "editorLineNumber.activeForeground": "#100e14a8",
    "editorIndentGuide.background1": "#100e1414",
    "editorWidget.background": "#fbfbfa",
    "scrollbarSlider.background": "#100e1418",
  },
});

monaco.editor.defineTheme("infinity-dark", {
  base: "vs-dark",
  inherit: true,
  rules: [],
  colors: {
    "editor.background": DARK_BG,
    "editorGutter.background": DARK_BG,
    "editor.lineHighlightBackground": "#ffffff08",
    "editorLineNumber.foreground": "#e8e6e359",
    "editorLineNumber.activeForeground": "#e8e6e3a8",
    "editorIndentGuide.background1": "#ffffff14",
    "editorWidget.background": "#191919",
    "scrollbarSlider.background": "#ffffff1f",
  },
});

// Hand the bundled instance to the React wrapper. Without this the wrapper goes
// to the CDN — this single call is the entire fix for that.
loader.config({ monaco });

export { monaco };
