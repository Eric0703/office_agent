/**
 * 硬件效果图:电子纸仿真两档逐视图截图(A0-7,产物存 docs/assets/)。
 * 竖向逻辑画布(08 §6.3):A/B 统一 300×400;画布禁滚动,内容超限截断或翻页。
 * clarify/result 经真实管线驱动:以页面 device_id 上传 L1 TASK-001.wav,
 * Gate 0 语义下(ASR 同音误转写)稳定先出 clarify,点选后出"已完成"结果。
 */
import { execSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ASSETS = path.resolve(HERE, "../../docs/assets");
const TASK_WAV = path.resolve(HERE, "../../testdata/l1_synthetic/audio/clean/TASK-001.wav");

/** 画布契约断言:竖向逻辑画布精确尺寸 + 禁滚动(overflow 两轴均 hidden) */
async function expectCanvas(page: Page, width: number, height: number): Promise<void> {
  const canvas = page.locator(".eink-canvas");
  await expect(canvas).toBeVisible();
  const actual = await canvas.evaluate((el) => {
    const style = getComputedStyle(el);
    return {
      w: el.clientWidth,
      h: el.clientHeight,
      overflowX: style.overflowX,
      overflowY: style.overflowY,
    };
  });
  expect(actual).toEqual({ w: width, h: height, overflowX: "hidden", overflowY: "hidden" });
}

test.beforeAll(() => {
  mkdirSync(ASSETS, { recursive: true });
  // 重置演示数据:两张卡片 + 当日简报,任务全开(clarify 需两个候选)
  execSync("cd .. && backend/.venv/bin/agent-host mock import", { stdio: "inherit" });
});

test("电子纸仿真 A 档(300×400 竖向):identity/cards/recording/clarify/result 截图", async ({
  page,
  browser,
  request,
}) => {
  // 1. 身份页:独立 context 中把 WS 挂起不连服务器(hello 无应答),保持配对中态
  const identityCtx = await browser.newContext({
    baseURL: "http://localhost:8000",
    viewport: { width: 420, height: 560 },
  });
  const identityPage = await identityCtx.newPage();
  await identityPage.routeWebSocket("**/ws", (ws) => {
    ws.onMessage(() => {});
  });
  await identityPage.goto("/?eink=a");
  await expect(identityPage.getByText(/电子纸仿真 300×400 竖向/)).toBeVisible();
  await expect(identityPage.getByText("张三")).toBeVisible();
  await expect(identityPage.getByText("连接主机中…")).toBeVisible();
  await identityPage.screenshot({ path: path.join(ASSETS, "eink-identity.png") });
  await identityCtx.close();

  await page.setViewportSize({ width: 420, height: 560 });

  // 2. 卡片页:hello → state.sync(2 张卡 + 简报 = 3 页);默认页单卡,页码 1/3
  //    排序:remind_at 最近优先——16:00 客户方案评审 < 18:00 周报撰写截止
  await page.goto("/?eink=a");
  await expectCanvas(page, 300, 400);
  await expect(page.locator(".p-status")).toHaveText(/已连接 · 1\/3/, { timeout: 20_000 });
  await expect(page.locator(".p-content h1")).toHaveText("客户方案评审");
  await page.screenshot({ path: path.join(ASSETS, "eink-cards.png") });

  // 3. 录音态:静态"录音中" + 纯黑实心条 + 点击结束(无每秒计时);
  //    <2s 停止,静默丢弃(不产生上传,避免干扰后续断言)
  await page.getByRole("button", { name: "录音" }).click();
  await expect(page.getByRole("button", { name: "点击结束" })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".rec-bar")).toBeVisible();
  await page.screenshot({ path: path.join(ASSETS, "eink-recording.png") });
  await page.getByRole("button", { name: "点击结束" }).click();
  await expect(page.getByRole("button", { name: "翻页" })).toBeVisible();

  // 4. clarify:以页面 device_id 上传 TASK-001.wav(Gate 0:同音误转写 → 候选澄清)
  const deviceId = await page.evaluate(() => localStorage.getItem("vbadge_device_id"));
  expect(deviceId).toBeTruthy();
  const recordId = crypto.randomUUID();
  const resp = await request.post(`/audio/${recordId}`, {
    headers: {
      "X-Device-Id": deviceId!,
      "X-Token": "dev",
      "X-Audio-Format": "wav",
      "X-Duration-Ms": "2470",
    },
    data: readFileSync(TASK_WAV),
  });
  expect(resp.ok()).toBeTruthy();
  const cand = page.getByRole("button", { name: "周报撰写" });
  await expect(cand).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("button", { name: "周报汇总" })).toBeVisible();
  await page.screenshot({ path: path.join(ASSETS, "eink-clarify.png") });

  // 5. 结果态:点选候选 → "已完成:周报撰写"
  await cand.click();
  await expect(page.getByText("已完成:周报撰写")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: path.join(ASSETS, "eink-result.png") });
});

test("电子纸仿真 B 档(300×400 竖向,布局同 A):cards/briefing/recording/clarify/result 截图", async ({
  page,
  request,
}) => {
  // 重置演示数据(A 档用例已完成周报撰写并撤卡,B 档需要完整 3 页)
  execSync("cd .. && backend/.venv/bin/agent-host mock import", { stdio: "inherit" });
  await page.setViewportSize({ width: 420, height: 560 });

  // 1. 卡片页:一屏一卡,页码 1/3(卡 1 / 卡 2 / 简报页);
  //    排序:remind_at 最近优先——16:00 客户方案评审 < 18:00 周报撰写截止
  await page.goto("/?eink=b");
  await expectCanvas(page, 300, 400);
  const nextBtn = page.getByRole("button", { name: "翻页" });
  await expect(page.locator(".p-status")).toHaveText(/已连接 · 1\/3/, { timeout: 20_000 });
  await expect(page.locator(".p-content h1")).toHaveText("客户方案评审");
  await page.screenshot({ path: path.join(ASSETS, "eink-b-cards.png") });

  // 2. 简报页:确认键翻页到 3/3(底部提示行即翻页提示);2/3 为次优先卡
  await nextBtn.click();
  await expect(page.locator(".p-status")).toHaveText(/2\/3/);
  await expect(page.locator(".p-content h1")).toHaveText("周报撰写截止");
  await nextBtn.click();
  await expect(page.locator(".p-status")).toHaveText(/3\/3/);
  await expect(page.locator(".p-content li")).toHaveCount(3);
  await page.screenshot({ path: path.join(ASSETS, "eink-b-briefing.png") });

  // 3. 录音态:纯黑实心条 + 静态"录音中" + 点击结束;<2s 停止静默丢弃
  await page.getByRole("button", { name: "录音" }).click();
  await expect(page.getByRole("button", { name: "点击结束" })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".rec-bar")).toBeVisible();
  await page.screenshot({ path: path.join(ASSETS, "eink-b-recording.png") });
  await page.getByRole("button", { name: "点击结束" }).click();
  await expect(nextBtn).toBeVisible();

  // 4. clarify:经真实管线(TASK-001.wav → Gate 0 候选澄清,≤2 个/页)
  const deviceId = await page.evaluate(() => localStorage.getItem("vbadge_device_id"));
  expect(deviceId).toBeTruthy();
  const recordId = crypto.randomUUID();
  const resp = await request.post(`/audio/${recordId}`, {
    headers: {
      "X-Device-Id": deviceId!,
      "X-Token": "dev",
      "X-Audio-Format": "wav",
      "X-Duration-Ms": "2470",
    },
    data: readFileSync(TASK_WAV),
  });
  expect(resp.ok()).toBeTruthy();
  const cand = page.getByRole("button", { name: "周报撰写" });
  await expect(cand).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("button", { name: "周报汇总" })).toBeVisible();
  await page.screenshot({ path: path.join(ASSETS, "eink-b-clarify.png") });

  // 5. 结果态:点选候选 → "已完成:周报撰写"
  await cand.click();
  await expect(page.getByText("已完成:周报撰写")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: path.join(ASSETS, "eink-b-result.png") });
});
