/// <reference types="vitest" />
import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import legacy from "@vitejs/plugin-legacy";
import path from "path";

// The React UI is served from the Python backend at / in production
// (live_captions.py mounts web/dist there as an SPA). In dev mode the
// proxy below forwards /api, /ws, and /results to the running backend
// on port 8765 so `pnpm dev` works without CORS.

export default defineConfig({
  base: "/",
  // `as PluginOption` works around pnpm's strict isolation: @vitejs/plugin-react
  // bundles its own peer copy of vite, so its returned `Plugin<any>` comes from
  // a different node_modules path than the `vite` we import here. Same version,
  // identical runtime — the cast just tells tsc to stop comparing path-distinct
  // type identities.
  plugins: [
    react() as PluginOption,
    // 🔴 vMix's Web Browser input is Chromium 51 (vMix 26.0.0.47, measured on
    // the mandir PC). It does not understand <script type="module"> and
    // SILENTLY IGNORES the tag — no error, no blank-screen failure, the page
    // just renders empty forever. Since the caption overlay IS a vMix browser
    // input, a module-only build means no captions on the hall screens and
    // nothing anywhere saying why.
    //
    // This emits a second, non-module bundle alongside the modern one, marked
    // `nomodule`. Modern browsers ignore that tag and run the module build;
    // Chromium 51 ignores the module tag and runs this one. Both work.
    //
    // Do not "tidy this up" by dropping the legacy target because the operator
    // UI looks fine in Chrome — the operator UI is not the surface that has to
    // work here. The overlay is.
    legacy({
      targets: ["chrome >= 51"],
      // Chromium 51 has arrow functions but no async/await and no optional
      // chaining, both of which this codebase and its dependencies use.
      modernPolyfills: false,
      renderLegacyChunks: true,
    }) as PluginOption,
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api":     { target: "http://localhost:8765", changeOrigin: true },
      "/ws":      { target: "ws://localhost:8765",   ws: true },
      "/results": { target: "http://localhost:8765", changeOrigin: true },
    },
  },
  // Tests run under plain node, with no jsdom and no browser.
  //
  // That is a constraint, not a shortcut. The overlay's real runtime is vMix's
  // Chromium 51; a jsdom environment would happily let a test pass while
  // leaning on a DOM feature that browser does not have. So the suite asserts
  // the parts that are honest to assert headlessly — what the store shows over
  // time, given a sequence of wire messages and a clock — and the DOM trim
  // stays covered by typecheck and build, as it was before.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    // The JS legacy target above does not cover CSS — plugin-legacy transpiles
    // script only. vMix's Chromium 51 also predates flexbox `gap` (Chrome 84),
    // `:is()` and `inset`, so a bundle that parses can still lay out wrongly.
    //
    // This stops the CSS pipeline emitting syntax that browser cannot parse.
    // It does NOT back-fill `gap` — nothing can. The overlay is safe because it
    // lays itself out with inline `position: absolute` and `left`/`top` rather
    // than flex or grid; the utilities that do use `gap` and `grid` live only
    // in the operator UI, which runs in a real browser. If the overlay is ever
    // rebuilt with flex utilities, that assumption breaks silently and the
    // captions will be mispositioned on the hall screens with no error.
    cssTarget: "chrome51",
  },
});
