import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// In development, /api/* is proxied to the FastAPI service with the prefix
// stripped (so /api/stats -> http://localhost:8000/stats). In Docker, nginx
// does the same (see nginx.conf). Override the target with VITE_API_TARGET.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: env.VITE_API_TARGET || "http://localhost:8000",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
  };
});
