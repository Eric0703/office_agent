import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { VitePWA } from "vite-plugin-pwa";

// 虚拟工牌(08 §4);dev 代理对齐登记册 §1.1 传输通道:WS /ws + HTTP /audio
export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "虚拟工牌",
        short_name: "虚拟工牌",
        start_url: "/",
        display: "standalone",
        background_color: "#f5f5f5",
        theme_color: "#000000",
      },
    }),
  ],
  server: {
    proxy: {
      "/ws": { target: "http://localhost:8000", ws: true },
      "/audio": { target: "http://localhost:8000" },
    },
  },
});
