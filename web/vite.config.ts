import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// The React UI is served from the Python backend at / in production
// (live_captions.py mounts web/dist there as an SPA). In dev mode the
// proxy below forwards /api, /ws, and /results to the running backend
// on port 8765 so `pnpm dev` works without CORS.

export default defineConfig({
  base: "/",
  plugins: [react()],
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
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
});
