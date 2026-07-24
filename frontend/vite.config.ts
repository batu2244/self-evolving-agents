import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    // dev-only: lets the QA tunnel (ngrok) reach the dev server
    allowedHosts: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
