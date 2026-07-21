/**
 * 浏览器端 e2e:单键录音闭环(FR-02 + A0 阻断 Bug 回归)。
 * 断言:(a) POST /audio 的 X-Device-Id 非空;(b) 响应非 401(应为 200);
 * (c) 界面最终出现 intent.result 结果页(任一结果态)。
 */
import { expect, test } from "@playwright/test";

test("单键录音 → 上传 → 结果页(device_id 非空、非 401)", async ({ page }) => {
  const deviceIds: (string | undefined)[] = [];
  const audioStatuses: number[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/audio/")) {
      deviceIds.push(req.headers()["x-device-id"]);
    }
  });
  page.on("response", (resp) => {
    if (resp.url().includes("/audio/")) {
      audioStatuses.push(resp.status());
    }
  });

  await page.goto("/");
  // idle:卡片页出现录音键(hello 认证通过后)
  const startBtn = page.getByRole("button", { name: "开始录音" });
  await expect(startBtn).toBeVisible({ timeout: 20_000 });

  // 第一次点击:开始录音;FR-02 三要素:红条 / 时长 / 点击结束提示
  // (首次 getUserMedia 在新 Chrome profile 初始化麦克风子系统可能需数秒)
  await startBtn.click();
  await expect(page.locator(".rec-bar")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".timer")).toHaveText(/^\d{2}:\d{2}$/, { timeout: 15_000 });
  const stopBtn = page.getByRole("button", { name: "点击结束" });
  await expect(stopBtn).toBeVisible();

  // 录 2.5s(>2s,避免误触丢弃)后再次点击:停止并上传
  await page.waitForTimeout(2_500);
  await stopBtn.click();

  // (a) X-Device-Id 非空;(b) 响应非 401
  await expect.poll(() => deviceIds.length, { timeout: 10_000 }).toBe(1);
  expect(deviceIds[0]).toBeTruthy();
  await expect.poll(() => audioStatuses.length, { timeout: 10_000 }).toBe(1);
  expect(audioStatuses[0]).not.toBe(401);
  expect(audioStatuses[0]).toBe(200);

  // (c) 出现 intent.result 结果页(虚拟麦克风音频无意义,任一终态均可)
  await expect(page.locator(".result h1")).toBeVisible({ timeout: 120_000 });
});
