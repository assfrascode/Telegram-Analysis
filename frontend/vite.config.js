import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = env.VITE_PROXY_TARGET || "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      proxy: {
        "/auth": proxyTarget,
        "/capacity": proxyTarget,
        "/uploads": proxyTarget,
        "/jobs": proxyTarget,
        "/question-sets": proxyTarget,
        "/telegram": proxyTarget,
        "/ws": {
          target: proxyTarget,
          ws: true,
        },
      },
    },
  };
});
