/**
 * 方案 A 三键模型 e2e(假 WS 主机 installFakeHost,确定性,不依赖真实 ASR):
 * 1. 连接后身份首页:姓名/部门/二维码,信息组水平+垂直居中(文字居中);
 * 2. 其余页(待办卡/简报):内容组垂直居中、文字左对齐;布局断言全为相对盒比较,不写绝对坐标;
 * 3. 三键数量与 hello capabilities(action/page_up/page_down);
 * 4. 主操作键状态复用:普通页短按开始录音/录音中短按结束、clarify 短按选定、
 *    clarify 长按取消(task:cancel,终态"已取消"不回候选页)、普通页长按回身份首页、
 *    失败结果页短按关闭、L2 确认页短按取消/长按确认;
 * 5. 澄清候选上/下双向移动;上翻/下翻页双向循环。
 * 物理键经 data-key 选择器定位;长按用 mouse.down 650ms 仿真(阈值 600ms);双击不实现。
 */
import { expect, test, type Locator, type Page } from "@playwright/test";

import { envelope, installFakeHost, type FakeHost } from "./seed";

/** 种子数据:页序列 = 身份(1) → 卡一(2) → 卡二(3) → 简报(4),共 4 页 */
const SEED = {
  cards: [
    { card_id: "c1", kind: "task", title: "卡一", body: "正文一", remind_at: "2026-07-22T10:00:00" },
    { card_id: "c2", kind: "timer", title: "卡二", remind_at: "2026-07-22T11:00:00" },
  ],
  briefing: {
    briefing_id: "b1",
    date: "2026-07-22",
    items: [{ kind: "event", title: "晨会", time: "09:30", source_id: "e1" }],
  },
};

/** 画布外虚拟物理键(三键:主操作键 action / 上翻 page_up / 下翻 page_down) */
function hwKey(page: Page, key: "action" | "page_up" | "page_down") {
  return page.locator(`.hw-keys .hw-key[data-key="${key}"]`);
}

/** 长按主操作键:mouse.down 650ms(>600ms 长按阈值)后 up */
async function longPressAction(page: Page): Promise<void> {
  const box = await hwKey(page, "action").boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(650);
  await page.mouse.up();
}

/** 假主机下发种子卡/简报并进入已连接态(停在身份首页 1/4) */
async function connectWithSeed(page: Page): Promise<FakeHost> {
  const host = await installFakeHost(page, SEED);
  await page.goto("/?eink=a");
  await expect(page.locator(".p-status")).toHaveText("已连接 · 1/4", { timeout: 10_000 });
  return host;
}

/** 推送一条 intent.result / confirm.request */
function pushResult(host: FakeHost, payload: Record<string, unknown>): void {
  host.ws!.send(envelope("intent.result", payload));
}

async function centerDelta(outer: Locator, inner: Locator): Promise<{ dx: number; dy: number }> {
  const o = await outer.boundingBox();
  const i = await inner.boundingBox();
  expect(o).toBeTruthy();
  expect(i).toBeTruthy();
  return {
    dx: Math.abs(i!.x + i!.width / 2 - (o!.x + o!.width / 2)),
    dy: Math.abs(i!.y + i!.height / 2 - (o!.y + o!.height / 2)),
  };
}

test("身份首页:信息组水平/垂直居中、文字居中;画布内无配对码与按钮", async ({ page }) => {
  await connectWithSeed(page);
  const content = page.locator(".p-content");
  const identity = page.locator(".p-identity");
  await expect(identity.getByText("张三")).toBeVisible();
  await expect(identity.getByText("研发部")).toBeVisible();
  await expect(identity.locator(".qr")).toBeVisible();
  const { dx, dy } = await centerDelta(content, identity);
  expect(dx).toBeLessThanOrEqual(8); // 水平居中
  expect(dy).toBeLessThanOrEqual(8); // 垂直居中
  await expect
    .poll(() => identity.evaluate((el) => getComputedStyle(el).textAlign))
    .toBe("center");
  await expect(content.locator(".paircode")).toHaveCount(0); // 身份首页不含配对码
  await expect(page.locator(".eink-canvas button")).toHaveCount(0); // 电子纸画布纯显示
});

test("待办卡/简报页:内容组垂直居中、文字左对齐", async ({ page }) => {
  await connectWithSeed(page);
  const content = page.locator(".p-content");
  const group = page.locator(".p-content .p-group").first();

  await hwKey(page, "page_down").click(); // 卡一页
  await expect(page.locator(".p-status")).toHaveText("已连接 · 2/4");
  await expect(group.locator("h1")).toHaveText("卡一");
  const card = await centerDelta(content, group);
  expect(card.dy).toBeLessThanOrEqual(8); // 垂直居中
  await expect
    .poll(() => group.evaluate((el) => getComputedStyle(el).textAlign))
    .toBe("left"); // 文字左对齐

  await hwKey(page, "page_down").click();
  await hwKey(page, "page_down").click(); // 简报页
  await expect(page.locator(".p-status")).toHaveText("已连接 · 4/4");
  await expect(group.locator("h1")).toHaveText("简报");
  const brief = await centerDelta(content, group);
  expect(brief.dy).toBeLessThanOrEqual(8);
  await expect
    .poll(() => group.evaluate((el) => getComputedStyle(el).textAlign))
    .toBe("left");
});

test("三键数量与 hello capabilities:action/page_up/page_down 三元组", async ({ page }) => {
  interface Capabilities {
    audio: { formats: string[]; channels: number };
    screen: { type: string; width: number; height: number; profile: string };
    keys: string[];
    led: boolean;
    haptics: boolean;
    network: string[];
  }
  const host = await installFakeHost(page);
  await page.goto("/?eink=a");
  await expect(page.locator(".p-status")).toHaveText(/已连接/, { timeout: 10_000 });

  // 恰好三枚物理键,无第四枚"确认·返回"
  await expect(page.locator(".hw-keys .hw-key")).toHaveCount(3);
  await expect(page.locator('.hw-keys .hw-key[data-key="confirm_back"]')).toHaveCount(0);
  await expect(hwKey(page, "action")).toBeVisible();
  await expect(hwKey(page, "page_up")).toBeVisible();
  await expect(hwKey(page, "page_down")).toBeVisible();

  expect(host.helloPayloads.length).toBeGreaterThanOrEqual(1);
  const caps = host.helloPayloads[0].capabilities as Capabilities;
  expect(caps.keys).toEqual(["action", "page_up", "page_down"]);
  expect(caps.screen).toEqual({ type: "eink", width: 300, height: 400, profile: "400x300" });
  expect(caps.audio.formats).toContain("webm-opus");
  expect(caps.audio.channels).toBe(1);
  expect(caps.led).toBe(true);
  expect(caps.haptics).toBe(true);
  expect(caps.network).toContain("wifi");
});

test("主操作键:普通页短按开始录音,录音中短按结束并上传", async ({ page }) => {
  const host = await connectWithSeed(page);
  // 假主机下设备未在真实服务器登记,上传经路由受理(200);显式断言上传发生且成功
  const uploads: { deviceId: string | undefined; status: number }[] = [];
  await page.route("**/audio/*", async (route) => {
    uploads.push({ deviceId: route.request().headers()["x-device-id"], status: 200 });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "accepted" }),
    });
  });
  await hwKey(page, "action").click(); // 普通页短按 = 开始录音
  await expect(page.locator(".rec-bar")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "录音中" })).toBeVisible();
  await expect(page.locator(".eink-canvas button")).toHaveCount(0); // 画布内无触摸按钮
  await expect
    .poll(() => host.messages.filter((m) => m.type === "record.start").length)
    .toBe(1);

  await page.waitForTimeout(2_500); // >2s,避免误触丢弃
  await hwKey(page, "action").click(); // 录音中短按 = 结束并上传
  await expect(page.locator(".rec-bar")).toHaveCount(0);
  await expect
    .poll(() => host.messages.filter((m) => m.type === "record.stop").length)
    .toBe(1);
  // 明确断言上传成功:一次 POST /audio,非空 X-Device-Id,200 受理
  await expect.poll(() => uploads.length).toBe(1);
  expect(uploads[0].deviceId).toBeTruthy();
  expect(uploads[0].status).toBe(200);
});

test("主操作键:clarify 短按选定当前高亮候选", async ({ page }) => {
  const host = await connectWithSeed(page);
  pushResult(host, {
    record_id: "r-clarify",
    status: "clarify",
    title: "选择一个任务",
    candidates: [
      { candidate_id: "cand-1", label: "候选一" },
      { candidate_id: "cand-2", label: "候选二" },
    ],
  });
  await expect(page.locator(".eink-canvas .candidate-item")).toHaveCount(2);
  await hwKey(page, "action").click(); // 短按 = 选定初始高亮(第 1 候选)
  await expect
    .poll(() => host.messages.filter((m) => m.type === "clarify.select").length)
    .toBe(1);
  const sel = host.messages.find((m) => m.type === "clarify.select");
  expect(sel?.payload?.record_id).toBe("r-clarify");
  expect(sel?.payload?.candidate_id).toBe("cand-1");
});

test("主操作键:clarify 长按取消——发 task:cancel,终态到达后不回候选页", async ({ page }) => {
  const host = await connectWithSeed(page);
  pushResult(host, {
    record_id: "r-cancel",
    status: "clarify",
    title: "选择一个任务",
    candidates: [
      { candidate_id: "cand-1", label: "候选一" },
      { candidate_id: "cand-2", label: "候选二" },
    ],
  });
  await expect(page.locator(".eink-canvas .candidate-item")).toHaveCount(2);
  await longPressAction(page); // 长按 = 取消并退出
  await expect
    .poll(() => host.messages.filter((m) => m.type === "clarify.select").length)
    .toBe(1);
  const sel = host.messages.find((m) => m.type === "clarify.select");
  expect(sel?.payload?.candidate_id).toBe("task:cancel");

  // 服务端回终态"已取消":覆盖层消失,不回到候选页
  pushResult(host, { record_id: "r-cancel", status: "success", title: "已取消" });
  await expect(page.locator(".eink-canvas .candidate-item")).toHaveCount(0);
  await expect(page.locator(".result")).toHaveCount(0, { timeout: 5_000 }); // 成功 3s 自动返回
});

test("主操作键:提醒类 clarify 长按发 remind:cancel", async ({ page }) => {
  const host = await connectWithSeed(page);
  pushResult(host, {
    record_id: "r-remind",
    status: "clarify",
    title: "创建哪个提醒?",
    candidates: [
      { candidate_id: "remind:confirm", label: "创建:明天 10:00" },
      { candidate_id: "remind:cancel", label: "取消" },
    ],
  });
  await expect(page.locator(".eink-canvas .candidate-item")).toHaveCount(2);
  await longPressAction(page);
  await expect
    .poll(() => host.messages.filter((m) => m.type === "clarify.select").length)
    .toBe(1);
  const sel = host.messages.find((m) => m.type === "clarify.select");
  expect(sel?.payload?.candidate_id).toBe("remind:cancel");
});

test("主操作键:普通页长按返回身份首页", async ({ page }) => {
  await connectWithSeed(page);
  await hwKey(page, "page_down").click();
  await expect(page.locator(".p-status")).toHaveText("已连接 · 2/4");
  await longPressAction(page);
  await expect(page.locator(".p-status")).toHaveText("已连接 · 1/4");
  await expect(page.locator(".p-content").getByText("张三")).toBeVisible();
});

test("主操作键:失败结果页短按关闭", async ({ page }) => {
  const host = await connectWithSeed(page);
  pushResult(host, {
    record_id: "r-failed",
    status: "failed",
    title: "未完成:没听清,请重说",
    error_code: "ASR_FAILED",
  });
  await expect(page.locator(".result")).toBeVisible();
  await expect(page.getByText("短按关闭")).toBeVisible();
  await hwKey(page, "action").click(); // 短按 = 关闭结果
  await expect(page.locator(".result")).toHaveCount(0);
  await expect(page.locator(".p-status")).toHaveText("已连接 · 1/4");
});

test("主操作键:L2 确认页短按取消、长按确认", async ({ page }) => {
  const host = await connectWithSeed(page);
  // 假主机下设备未在真实服务器登记,上传经路由直接受理(200),驱动状态机到 processing
  await page.route("**/audio/*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "accepted" }),
    }),
  );
  const pushConfirm = (id: string) =>
    host.ws!.send(
      envelope("confirm.request", { confirm_id: id, title: "删除重要文件?", body: "不可恢复" }),
    );
  /** confirm.request 只在 uploading/processing 迁移到 confirm_wait(登记册时序):先走真实录音上传 */
  const driveToProcessing = async () => {
    await hwKey(page, "action").click();
    await expect(page.locator(".rec-bar")).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_500);
    await hwKey(page, "action").click();
    await expect(page.locator(".rec-bar")).toHaveCount(0);
  };

  await driveToProcessing();
  pushConfirm("cf-1");
  await expect(page.getByText("长按确认执行 · 短按取消")).toBeVisible();
  await hwKey(page, "action").click(); // 短按 = 取消
  await expect
    .poll(() => host.messages.filter((m) => m.type === "confirm.response").length)
    .toBe(1);
  let resp = host.messages.find((m) => m.type === "confirm.response");
  expect(resp?.payload).toMatchObject({ confirm_id: "cf-1", decision: "cancel" });

  // 服务端回终态(与真实流程一致):3s 自动返回 idle,方可第二轮录音
  pushResult(host, { record_id: "r-l2-1", status: "success", title: "已取消" });
  await expect(page.locator(".result")).toHaveCount(0, { timeout: 5_000 });

  await driveToProcessing();
  pushConfirm("cf-2");
  await expect(page.getByText("长按确认执行 · 短按取消")).toBeVisible();
  await longPressAction(page); // 长按 = 确认
  await expect
    .poll(() => host.messages.filter((m) => m.type === "confirm.response").length)
    .toBe(2);
  resp = host.messages.filter((m) => m.type === "confirm.response").at(-1);
  expect(resp?.payload).toMatchObject({ confirm_id: "cf-2", decision: "confirm" });
});

test("澄清候选双向移动:下翻×2 高亮第 3、上翻×1 高亮第 2、短按选定第 2", async ({ page }) => {
  const host = await connectWithSeed(page);
  pushResult(host, {
    record_id: "r-clarify3",
    status: "clarify",
    title: "选择一个任务",
    candidates: [
      { candidate_id: "cand-1", label: "候选一" },
      { candidate_id: "cand-2", label: "候选二" },
      { candidate_id: "cand-3", label: "候选三" },
    ],
  });
  const hl = page.locator(".eink-canvas .candidate-item.hl");
  await expect(hl).toHaveText(/候选一/);

  await hwKey(page, "page_down").click();
  await hwKey(page, "page_down").click();
  await expect(hl).toHaveText(/候选三/);
  await hwKey(page, "page_up").click();
  await expect(hl).toHaveText(/候选二/);

  await hwKey(page, "action").click();
  await expect
    .poll(() => host.messages.filter((m) => m.type === "clarify.select").length)
    .toBe(1);
  const sel = host.messages.find((m) => m.type === "clarify.select");
  expect(sel?.payload?.candidate_id).toBe("cand-2");
});

test("上翻/下翻双向循环:身份 → 卡一 → 卡二 → 简报 → 绕回身份,上翻逆向", async ({ page }) => {
  await connectWithSeed(page);
  const status = page.locator(".p-status");
  const h1 = page.locator(".p-content h1");

  await hwKey(page, "page_down").click();
  await expect(status).toHaveText("已连接 · 2/4");
  await expect(h1).toHaveText("卡一");
  await hwKey(page, "page_down").click();
  await expect(status).toHaveText("已连接 · 3/4");
  await expect(h1).toHaveText("卡二");
  await hwKey(page, "page_down").click();
  await expect(status).toHaveText("已连接 · 4/4");
  await expect(h1).toHaveText("简报");
  await hwKey(page, "page_down").click(); // 继续下翻:绕回身份首页
  await expect(status).toHaveText("已连接 · 1/4");
  await expect(page.locator(".p-content").getByText("张三")).toBeVisible();

  await hwKey(page, "page_up").click(); // 上翻逆向循环:身份 → 简报
  await expect(status).toHaveText("已连接 · 4/4");
  await expect(h1).toHaveText("简报");
  await hwKey(page, "page_up").click();
  await expect(status).toHaveText("已连接 · 3/4");
  await expect(h1).toHaveText("卡二");
});
