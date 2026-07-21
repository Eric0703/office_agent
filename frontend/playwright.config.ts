import { defineConfig } from "@playwright/test";

// 浏览器端 e2e(Owner 明确要求;Playwright 仅 devDependency)。
// 复用系统 Chrome(channel: "chrome"),不下载浏览器;
// 虚拟麦克风(fake device)让 getUserMedia 在 localhost 安全上下文直接可用。
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  workers: 1, // 单实例:后端 SQLite 与录音处理为共享状态
  reporter: "list",
  webServer: {
    // 隔离测试服务:独立端口 8100 + 临时数据库(frontend/.e2e-runtime,每次重建);
    // 不触碰 Owner 的 8000 服务与根 data/agent.db,不复用任何已在运行的服务
    command: "bash e2e/start-test-server.sh",
    url: "http://localhost:8100/health",
    reuseExistingServer: false,
    timeout: 60_000,
  },
  use: {
    baseURL: "http://localhost:8100",
    channel: "chrome",
    launchOptions: {
      args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
    },
    permissions: ["microphone"],
  },
});
