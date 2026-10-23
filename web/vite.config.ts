import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";

// Phones open the web app on the laptop's LAN address, so /api, /files and /ws are proxied to FastAPI.
export default defineConfig({
  // Phone cameras only work on HTTPS (self-signed cert, accept once per phone). On the laptop, http://localhost is already a
  // secure context, so `npm run dev:local` serves plain HTTP with no certificate warning.
  plugins: process.env.PLAIN_HTTP ? [react()] : [react(), basicSsl()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8010", rewrite: (p) => p.replace(/^\/api/, "") },
      "/files": "http://127.0.0.1:8010",
      "/ws": { target: "ws://127.0.0.1:8010", ws: true },
    },
  },
});
