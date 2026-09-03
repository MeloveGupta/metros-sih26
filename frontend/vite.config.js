import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API calls to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/scan": "http://localhost:8000",
      "/scans": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
