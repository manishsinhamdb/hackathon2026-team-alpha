import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

export default defineConfig({
  // Component render tests (.test.tsx) opt into jsdom via a per-file `// @vitest-environment jsdom`
  // docblock; the default stays `node` for the pure lib tests.
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    include: ["src/test/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
});
