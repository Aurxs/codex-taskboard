import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "./",
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL("../dist/web", import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // The injected frame is deliberately sandboxed and therefore sends the
    // opaque `null` origin in development. Keep this allow-list explicit;
    // do not fall back to `*`, which would make credentialed local requests
    // ambiguous.
    cors: {
      origin: ["null", "http://127.0.0.1:5173", "http://localhost:5173"],
    },
    proxy: {
      "/api": `http://127.0.0.1:${process.env.CODEX_TASKBOARD_PORT || "47823"}`,
    },
  },
});
