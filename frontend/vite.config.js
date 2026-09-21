import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const workspaceEnv = loadEnv(mode, "../", "");
  const proxyTarget = env.VITE_PROXY_TARGET || "http://localhost:8000";
  const reportWindow = Number(
    process.env.MAX_TELEGRAM_REPORT_WINDOW
      || workspaceEnv.MAX_TELEGRAM_REPORT_WINDOW
      || env.MAX_TELEGRAM_REPORT_WINDOW
      || 30,
  );
  if (!Number.isInteger(reportWindow) || reportWindow < 1) {
    throw new Error("MAX_TELEGRAM_REPORT_WINDOW must be a positive whole number of days");
  }

  return {
    plugins: [react()],
    define: {
      __MAX_TELEGRAM_REPORT_WINDOW__: JSON.stringify(reportWindow),
    },
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
