import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: proxy the checkpoint API. VITE_MOCK=1 -> mock server on :8001, else the real checkpoint on :8000.
// Production: FastAPI serves web/dist on the same origin, so every fetch uses a relative path.
const target = process.env.VITE_MOCK === "1" ? "http://localhost:8001" : "http://localhost:8000";

const API_PATHS = [
  "tool", "health", "status", "block", "alerts", "restore", "stream", "heartbeat", "config",
  "policy", "incidents", "guardrail", "audit", "evidence", "demo", "guild",
];

// Regex keys match the path segment exactly (so "/tool" never catches "/tooltip.tsx").
const proxy = Object.fromEntries(
  API_PATHS.map((p) => [`^/${p}(/.*)?(\\?.*)?$`, { target, changeOrigin: true }]),
);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: false, proxy },
  preview: { proxy },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
