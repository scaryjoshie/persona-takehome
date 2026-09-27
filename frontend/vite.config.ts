import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: Vite on 5173 proxies the API and both WebSockets to FastAPI on 8000 (BACKEND=localhost:8768
// to test a branch's server alongside main's).
// Prod: FastAPI serves dist/ from the same origin, so every URL stays relative.
const backend = process.env.BACKEND ?? "localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: `http://${backend}`, changeOrigin: true },
      "/ws": { target: `ws://${backend}`, ws: true, changeOrigin: true },
    },
  },
});
