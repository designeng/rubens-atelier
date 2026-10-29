/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// `make web-dev` serves the board at :5173/board/ and forwards API calls to `make api` on :8000.
const api = "http://localhost:8000";

export default defineConfig({
  base: "/board/",
  plugins: [vue()],
  build: { outDir: "../app/static/dist", emptyOutDir: true },
  server: {
    proxy: Object.fromEntries(["/projects", "/items", "/requests", "/scheduler", "/github"].map((p) => [p, api])),
  },
  test: { environment: "node" },
});
