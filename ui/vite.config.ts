import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3010,
    strictPort: true,
    // The dashboard talks to the FastAPI server through this proxy, so the
    // browser only ever sees one origin and there is nothing to configure.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
      },
    },
  },
});
