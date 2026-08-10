import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Dev server proxies API/auth calls straight to the FastAPI backend
// (app/main.py, port 3003) so the frontend never needs CORS config.
export default defineConfig(({ command }) => ({
  base: "/api/manager-assistant-dashboard/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api/auth": "http://localhost:3003",
      "/api/projects": "http://localhost:3003",
      "/api/events": "http://localhost:3003",
      "/api/todos": "http://localhost:3003",
      "/api/integrations": "http://localhost:3003",
      "/api/agents": "http://localhost:3003",
      "/api/chat": "http://localhost:3003",
      "/api/dev": "http://localhost:3003",
      "/api/blocklist": "http://localhost:3003",
      "/api/employees": "http://localhost:3003",
      "/api/portfolios": "http://localhost:3003",
      "/api/meetings": "http://localhost:3003",
      "/api/workload": "http://localhost:3003",
      "/api/scheduler": "http://localhost:3003",
      "/api/followups": "http://localhost:3003",
      "/api/brief": "http://localhost:3003",
      "/api/heartbeat": "http://localhost:3003",
      "/api/time": "http://localhost:3003",
      "/auth": "http://localhost:3003",
    },
  },
}));
