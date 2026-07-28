/**
 * 硬件效果图:电子纸仿真两档逐视图截图(A0-7,产物存 docs/v2.0/assets/)。
 * 竖向逻辑画布(08 §6.3):A/B 统一 300×400;画布禁滚动,内容超限截断或翻页。
 * 方案 A 单屏 AI 工牌:页序列 = 身份首页(默认) → 待办卡 → 简报,上翻/下翻双向循环;
 * A/B 仅是屏幕档位(B 档同样有身份首页),配对等待呈现不在本用例范围。
 * 虚拟工牌硬件模拟:画布为纯屏幕(无触摸按钮),全部输入经画布外 .hw-keys 物理键
 * (主操作键/上翻/下翻,data-key 定位,语义同未来 ESP32 GPIO);截图对象为 .eink-frame。
 * clarify/result 经真实管线驱动:以页面 device_id 上传 L1 TASK-001.wav,
 * Gate 0 语义下(ASR 同音误转写)稳定先出 clarify,短按主操作键选定后出"已完成"结果。
 * 数据在隔离测试服务(8100 端口 + frontend/.e2e-runtime)内重置,不碰真实数据库。
 */
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { reseed } from "./seed";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ASSETS = path.resolve(HERE, "../../docs/v2.0/assets");
const TASK_WAV = path.resolve(HERE, "../../testdata/l1_synthetic/audio/clean/TASK-001.wav");

/** 画布外虚拟物理键(主操作键 action / 上翻 page_up / 下翻 page_down(三键)) */
function hwKey(page: Page, key: string) {
  return page.locator(`.hw-keys .hw-key[data-key="${key}"]`);
}

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

/** 无触屏契约:画布(纯屏幕)内不得存在录音相关触摸按钮 */
async function expectNoCanvasTouchButtons(page: Page): Promise<void> {
  await expect(page.locator(".eink-canvas button", { hasText: "开始录音" })).toHaveCount(0);
  await expect(page.locator(".eink-canvas button", { hasText: "点击结束" })).toHaveCount(0);
}

/** 截图对象:.eink-frame(画布 + 物理键,完整呈现虚拟设备) */
async function shotFrame(page: Page, name: string): Promise<void> {
  await page.locator(".eink-frame").screenshot({ path: path.join(ASSETS, name) });
}

/** clarify 候选选择(无触屏):下翻键移动高亮到目标候选,短按主操作键选定 */
async function selectCandidate(page: Page, label: string): Promise<void> {
  const highlighted = page.locator(".eink-canvas .candidate-item.hl");
  for (let i = 0; i < 5; i++) {
    if ((await highlighted.textContent())?.includes(label)) {
      break;
    }
    await hwKey(page, "page_down").click();
  }
  await expect(highlighted).toContainText(label);
  await hwKey(page, "action").click();
}

/** 经真实管线上传 TASK-001.wav 触发 clarify(Gate 0:同音误转写 → 候选澄清) */
async function uploadClarifyWav(page: Page, request: APIRequestContext): Promise<void> {
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
  await expect(
    page.locator(".eink-canvas .candidate-item", { hasText: "周报撰写" }),
  ).toBeVisible({ timeout: 120_000 });
  await expect(
    page.locator(".eink-canvas .candidate-item", { hasText: "周报汇总" }),
  ).toBeVisible();
}

test.beforeAll(() => {
  mkdirSync(ASSETS, { recursive: true });
  // 重置演示数据:两张卡片 + 当日简报,任务全开(clarify 需两个候选);仅影响隔离库
  reseed();
});

test("电子纸仿真 A 档(300×400 竖向):identity/cards/recording/clarify/result 截图", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 420, height: 560 });

  // 1. 身份首页(方案 A 默认页):hello → state.sync 后停在页 0;姓名/部门,不含配对码
  await page.goto("/?eink=a");
  await expectCanvas(page, 300, 400);
  await expect(page.locator(".p-status")).toHaveText(/已连接 · 1\/\d+/, { timeout: 20_000 });
  await expect(page.locator(".p-content").getByText("张三")).toBeVisible();
  await expect(page.locator(".p-content").getByText("研发部")).toBeVisible();
  await expectNoCanvasTouchButtons(page);
  await shotFrame(page, "eink-identity.png");

  // 2. 卡片页:下翻键进入;排序 remind_at 最近优先——"客户方案评审"(今天 16:00)应为页 2
  await hwKey(page, "page_down").click();
  await expect(page.locator(".p-status")).toHaveText(/已连接 · 2\/\d+/);
  await expect(page.locator(".p-content h1")).toHaveText("客户方案评审");
  await shotFrame(page, "eink-cards.png");

  // 3. 录音态:物理录音键开始/结束;静态"录音中" + 纯黑实心条,画布内无"点击结束"(无每秒计时);
  //    <2s 停止,静默丢弃(不产生上传,避免干扰后续断言)
  await hwKey(page, "action").click();
  await expect(page.locator(".rec-bar")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "录音中" })).toBeVisible();
  await expectNoCanvasTouchButtons(page);
  await shotFrame(page, "eink-recording.png");
  await hwKey(page, "action").click();
  await expect(page.locator(".p-status")).toHaveText(/已连接 · 2\/\d+/); // 页序号保持(仍在卡片页)
  await expect(hwKey(page, "page_down")).toBeEnabled();

  // 4. clarify:以页面 device_id 上传 TASK-001.wav(Gate 0:同音误转写 → 候选澄清)
  await uploadClarifyWav(page, request);
  await shotFrame(page, "eink-clarify.png");

  // 5. 结果态:下翻键移动高亮 + 短按主操作键选定 → "已完成:周报撰写"
  await selectCandidate(page, "周报撰写");
  await expect(page.getByText("已完成:周报撰写")).toBeVisible({ timeout: 30_000 });
  await shotFrame(page, "eink-result.png");
});

test("电子纸仿真 B 档(300×400 竖向,布局同 A):identity/cards/briefing/recording/clarify/result 截图", async ({
  page,
  request,
}) => {
  // 重置演示数据(A 档用例已完成周报撰写并撤卡,B 档需要完整页序列);仅影响隔离库
  reseed();
  await page.setViewportSize({ width: 420, height: 560 });

  // 1. 身份首页(方案 A:B 档同样有身份首页,B 仅是屏幕档位)
  await page.goto("/?eink=b");
  await expectCanvas(page, 300, 400);
  await expect(page.locator(".p-status")).toHaveText(/已连接 · 1\/\d+/, { timeout: 20_000 });
  await expect(page.locator(".p-content").getByText("张三")).toBeVisible();
  await expectNoCanvasTouchButtons(page);
  await shotFrame(page, "eink-b-identity.png");

  // 2. 卡片页:下翻键进入;"客户方案评审"(今天 16:00)为页 2
  await hwKey(page, "page_down").click();
  await expect(page.locator(".p-status")).toHaveText(/已连接 · 2\/\d+/);
  await expect(page.locator(".p-content h1")).toHaveText("客户方案评审");
  await shotFrame(page, "eink-b-cards.png");

  // 3. 简报页:下翻键翻页直到"简报"(页数随真实数据变化,上限保护)
  for (let i = 0; i < 6; i++) {
    if ((await page.locator(".p-content h1").textContent()) === "简报") {
      break;
    }
    await hwKey(page, "page_down").click();
  }
  await expect(page.locator(".p-content h1")).toHaveText("简报");
  await expect(page.locator(".p-content li")).toHaveCount(3);
  await shotFrame(page, "eink-b-briefing.png");

  // 4. 录音态:物理录音键开始/结束;纯黑实心条 + 静态"录音中",画布内无"点击结束";
  //    <2s 停止静默丢弃
  await hwKey(page, "action").click();
  await expect(page.locator(".rec-bar")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "录音中" })).toBeVisible();
  await expectNoCanvasTouchButtons(page);
  await shotFrame(page, "eink-b-recording.png");
  await hwKey(page, "action").click();
  await expect(page.locator(".p-status")).toHaveText(/已连接 · \d+\/\d+/);
  await expect(hwKey(page, "page_down")).toBeEnabled();

  // 5. clarify:经真实管线(TASK-001.wav → Gate 0 候选澄清,≤2 个/屏,高亮随上翻/下翻移动)
  await uploadClarifyWav(page, request);
  await shotFrame(page, "eink-b-clarify.png");

  // 6. 结果态:下翻键移动高亮 + 短按主操作键选定 → "已完成:周报撰写"
  await selectCandidate(page, "周报撰写");
  await expect(page.getByText("已完成:周报撰写")).toBeVisible({ timeout: 30_000 });
  await shotFrame(page, "eink-b-result.png");
});
