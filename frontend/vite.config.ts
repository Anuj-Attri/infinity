import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

// Single-source the app version from package.json into the bundle.
const APP_VERSION: string = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
).version;

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  define: { __APP_VERSION__: JSON.stringify(APP_VERSION) },

  server: {
    port: 5173,
    strictPort: true,
  },

  build: {
    // Mark 3 shipped ONE 1.72 MB chunk. That was invisible on a desktop app and
    // is a multi-second blank screen on the web, which is what this is now.
    // Splitting the big, rarely-first-needed libraries out means the shell can
    // paint before they arrive.
    //
    // This only helps if the code also loads them lazily — a static import
    // still blocks first paint no matter which chunk it lands in. Wiring
    // React.lazy around the editor is the other half, and belongs with the
    // workbench work.
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          markdown: ["react-markdown", "remark-gfm", "react-syntax-highlighter"],
          // Monaco is bundled now rather than pulled from a CDN, so this chunk
          // went from a few kB of React wrapper to the whole editor engine.
          // That is only affordable because TileGrid imports CodeTileLazy —
          // nothing static references this chunk, so the browser fetches it the
          // first time someone opens the Code tile and never otherwise.
          editor: ["@monaco-editor/react", "monaco-editor"],
        },
      },
    },
    // The default is 500 kB and Mark 3 blew past it silently. Keep the warning
    // meaningful rather than raising it until it stops complaining.
    chunkSizeWarningLimit: 700,
  },
});
