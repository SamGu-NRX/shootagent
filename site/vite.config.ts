import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The marketing site for ShootAgent: a static build, deployed on its own (vercel.json). The product's UI lives in
// ../web and runs at the live demo it links to.
export default defineConfig({
  plugins: [react()],
  build: { target: "es2022", assetsInlineLimit: 0 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
