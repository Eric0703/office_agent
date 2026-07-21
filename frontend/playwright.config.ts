import { defineConfig } from "@playwright/test";

// 浏览器端 e2e(Owner 明确要求;Playwright 仅 devDependency)。
// 复用系统 Chrome(channel: "chrome"),不下载浏览器;
// 虚拟麦克风(fake device)让 getUserMedia 在 localhost 安全上下文直接可用。
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  workers: 1, // 单实例:后端 SQLite 与录音处理为共享状态
  reporter: "list",
  use: {
    baseURL: "http://localhost:8000",
    channel: "chrome",
    launchOptions: {
      args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
    },
    permissions: ["microphone"],
  },
  webServer: {
    // 真实 agent-host serve(配置取仓库根 config.yaml,dev_mode=auto_approve);
    // 先重置 mock 数据保证用例可重复;复用已在跑的服务(Owner 手动起服时)
    command:
      "cd .. && backend/.venv/bin/agent-host mock import >/dev/null && backend/.venv/bin/agent-host serve",
    url: "http://localhost:8000/health",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
