/**
 * 浏览器端 e2e:单键录音 UI 闭环(FR-02)。
 * 虚拟麦克风只验证"录音按钮 / 上传 / UI 状态机",不验证转写内容;
 * 真实 ASR→草稿链路见 backend tests/test_fr05_field_note_desk.py。
 * 断言:(a) 静态录音页契约:黑条 / "录音中" / "点击结束",无每秒计时(08 §6.1);
 * (b) POST /audio 的 X-Device-Id 非空且响应 200;
 * (c) 最终收到并显示 intent.result;失败态停留,点击返回(不闪退)。
 */
import { expect, test } from "@playwright/test";

test("单键录音 → 上传 → 结果页(device_id 非空、200、结果可见可返回)", async ({ page }) => {
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

  // 第一次点击:开始录音;静态录音页三要素(无计时刷新)
  await startBtn.click();
  await expect(page.locator(".rec-bar")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "录音中" })).toBeVisible();
  await expect(page.locator(".timer")).toHaveCount(0); // 已静态化,不得再出现计时元素
  const stopBtn = page.getByRole("button", { name: "点击结束" });
  await expect(stopBtn).toBeVisible();

  // 录 2.5s(>2s,避免误触丢弃)后再次点击:停止并上传
  await page.waitForTimeout(2_500);
  await stopBtn.click();

  // (a) X-Device-Id 非空;(b) 响应非 401(应为 200)
  await expect.poll(() => deviceIds.length, { timeout: 10_000 }).toBe(1);
  expect(deviceIds[0]).toBeTruthy();
  await expect.poll(() => audioStatuses.length, { timeout: 10_000 }).toBe(1);
  expect(audioStatuses[0]).not.toBe(401);
  expect(audioStatuses[0]).toBe(200);

  // (c) 出现 intent.result 结果页(虚拟麦克风音频无意义,任一终态均可)
  const result = page.locator(".result");
  await expect(result).toBeVisible({ timeout: 120_000 });
  await expect(page.locator(".result h1")).not.toBeEmpty();

  // 返回原页面:成功 3s 自动返回;失败/未听清停留,点击返回(08 §6.1)
  await expect(async () => {
    if (await result.isVisible()) {
      await result.click();
    }
    await expect(startBtn).toBeVisible();
  }).toPass({ timeout: 15_000 });
});

test("eink 档:画布内无录音触摸按钮,物理录音键开始/停止并上传", async ({ page }) => {
  const audioUploads: (string | undefined)[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/audio/")) {
      audioUploads.push(req.headers()["x-device-id"]);
    }
  });

  await page.goto("/?eink=a");
  // hello 认证通过、进入 idle(状态栏显示已连接)后物理键可用
  await expect(page.locator(".p-status")).toHaveText(/已连接/, { timeout: 20_000 });
  const recKey = page.locator('.hw-keys .hw-key[data-key="action"]');
  await expect(recKey).toBeEnabled();

  // 无触屏契约:画布(纯屏幕)内不存在录音触摸按钮
  await expect(page.locator(".eink-canvas button", { hasText: "开始录音" })).toHaveCount(0);
  await expect(page.locator(".eink-canvas button", { hasText: "点击结束" })).toHaveCount(0);

  // 物理录音键按下:开始录音,.rec-bar 出现;画布内仍无"点击结束"
  await recKey.click();
  await expect(page.locator(".rec-bar")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "录音中" })).toBeVisible();
  await expect(page.locator(".eink-canvas button", { hasText: "点击结束" })).toHaveCount(0);

  // 录 2.5s(>2s,避免误触丢弃)后再按同一键:停止,.rec-bar 消失,产生上传(X-Device-Id 非空)
  await page.waitForTimeout(2_500);
  await recKey.click();
  await expect(page.locator(".rec-bar")).toHaveCount(0);
  await expect.poll(() => audioUploads.length, { timeout: 10_000 }).toBe(1);
  expect(audioUploads[0]).toBeTruthy();
});
