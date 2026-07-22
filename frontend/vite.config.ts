import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Dev server proxies API/auth calls straight to the FastAPI backend
// (app/main.py, port 3003) so the frontend never needs CORS config.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:3003",
      "/auth": "http://localhost:3003",
    },
  },
});
