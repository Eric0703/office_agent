import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { VitePWA } from "vite-plugin-pwa";

// 虚拟工牌(08 §4);dev 代理对齐登记册 §1.1 传输通道:WS /ws + HTTP /audio
export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: "autoUpdate",
      // 发布即更新(验收阻断修复):新 SW 激活即接管并清理旧预缓存,
      // 配合 autoUpdate 刷新页面,避免浏览器长期停留在旧 bundle
      workbox: {
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
      },
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
