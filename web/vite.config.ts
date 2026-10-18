import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  base: "/",
  // Older phones (iOS 13+, Android Chrome 80+) must run the demo chat, so compile down.
  build: { outDir: "dist", emptyOutDir: true, target: ["es2019", "safari13", "chrome80", "firefox78"] },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      // Only /demo/<something> goes to the backend; the bare /demo path is the SPA chat page.
      "^/demo/.+": { target: backend, changeOrigin: true },
    },
  },
});
