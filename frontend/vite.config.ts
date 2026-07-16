import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendTarget = process.env.CABINET_DEV_BACKEND_ORIGIN ?? "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: {
      "/api": {
        target: backendTarget,
        changeOrigin: true,
      },
      "/ws": {
        target: backendTarget,
        ws: true,
      },
    },
  },
});
