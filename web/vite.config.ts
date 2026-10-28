import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The FastAPI server (`shootagent serve`) listens on :8000; the UI calls same-origin /api.
const API = process.env.SHOOTAGENT_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: API, changeOrigin: true } },
  },
  preview: {
    proxy: { "/api": { target: API, changeOrigin: true } },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
