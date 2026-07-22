/**
 * FR-02 弱网(08 §1.1 OfflineCached):断网录音 → 离线缓存(IndexedDB)→ 恢复连接自动补传。
 * 虚拟麦克风只验证上传/补传链路,不验证转写内容(真机链路由后端测试覆盖);
 * 隔离测试服务(8100 + 临时库),不碰真实数据。
 */
import { expect, test } from "@playwright/test";

import { envelope, installFakeHost, queueLength, seedPendingAudio } from "./seed";

test("断网录音:离线缓存,恢复后自动补传并收到结果", async ({ page, context }) => {
  const uploads: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes("/audio/")) {
      uploads.push(req.headers()["x-device-id"] ?? "");
    }
  });

  await page.goto("/");
  const startBtn = page.getByRole("button", { name: "开始录音" });
  await expect(startBtn).toBeVisible({ timeout: 20_000 });

  // 录音 2.5s(>2s,避免误触丢弃)
  await startBtn.click();
  await expect(page.getByRole("button", { name: "点击结束" })).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(2_500);

  // 断网后结束:上传失败 → 音频入离线队列并提示(状态提示与通知文案相同,取其一)
  await context.setOffline(true);
  await page.getByRole("button", { name: "点击结束" }).click();
  await expect(page.getByText(/已离线缓存/).first()).toBeVisible({ timeout: 10_000 });

  // 恢复网络:WS 重连 → hello → state.sync → 自动补传(带非空 X-Device-Id);
  // 清空计数,只认恢复后的真实补传(断网时的失败尝试也可能产生 request 事件)
  uploads.length = 0;
  await context.setOffline(false);
  await expect.poll(() => uploads.length, { timeout: 45_000 }).toBeGreaterThan(0);
  expect(uploads[0]).toBeTruthy();

  // 补传后管线处理,结果页可见(虚拟麦克风静音 → 未听清/失败任一终态,停留不闪退)
  await expect(page.locator(".result h1")).toBeVisible({ timeout: 60_000 });
  await page.locator(".result").click();
  await expect(startBtn).toBeVisible({ timeout: 10_000 });
});

test("补传遇 503:自动退避重试直至成功(可重试错误分级)", async ({ page }) => {
  let attempts = 0;
  await page.route("**/audio/*", async (route) => {
    attempts += 1;
    if (attempts <= 2) {
      // 原始上传 + 首次补传均 503(暂时性故障):应入队并自动退避重试
      await route.fulfill({ status: 503 });
      return;
    }
    await route.continue();
  });

  await page.goto("/");
  const startBtn = page.getByRole("button", { name: "开始录音" });
  await expect(startBtn).toBeVisible({ timeout: 20_000 });
  await startBtn.click();
  await expect(page.getByRole("button", { name: "点击结束" })).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(2_500);
  await page.getByRole("button", { name: "点击结束" }).click();

  // 503 → 离线缓存提示 → 重连后补传再 503 → 退避重试成功 → 结果页可见
  await expect(page.getByText(/已离线缓存/).first()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".result h1")).toBeVisible({ timeout: 60_000 });
  expect(attempts).toBeGreaterThanOrEqual(3); // 原始 503 + 补传 503 + 重试成功
  await page.locator(".result").click();
  await expect(startBtn).toBeVisible({ timeout: 10_000 });
});

test("队列只在终态结果到达后出队(A1-2,无真实 ASR 依赖)", async ({ page }) => {
  // 假 WS 主机(hello ok + state.sync)+ 路由受理上传:时序完全确定,不依赖真实转写
  const host = await installFakeHost(page);
  const recordId = "e2e-queue-gate";
  let posts = 0;
  await page.route(`**/audio/${recordId}`, async (route) => {
    posts += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "received", record_id: recordId }),
    });
  });

  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 10_000 });
  await seedPendingAudio(page, recordId);
  await page.reload();
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 10_000 });

  // 补传受理(200)后:终态未到,队列必须保留(受理≠终态)
  await expect.poll(() => posts, { timeout: 10_000 }).toBe(1);
  expect(await queueLength(page)).toBe(1);

  // 终态 intent.result 到达 → 结果页可见且出队
  host.ws!.send(
    envelope("intent.result", { record_id: recordId, status: "success", title: "已处理完成" }),
  );
  await expect(page.locator(".result h1")).toBeVisible({ timeout: 10_000 });
  await expect.poll(() => queueLength(page), { timeout: 10_000 }).toBe(0);
});

test("正常上传 200 后关闭页面:重开自动补传并恢复结果(A1-2)", async ({ page }) => {
  const host = await installFakeHost(page);
  let posts = 0;
  let recordId = "";
  await page.route("**/audio/**", async (route, request) => {
    posts += 1;
    recordId = request.url().split("/").pop() ?? "";
    if (posts === 1) {
      // 原始上传受理(随后页面关闭,结果未送达)
      await route.fulfill({ json: { status: "received", record_id: recordId } });
      return;
    }
    // 重开后补传:duplicate 受理,并模拟服务端补推缓存的终态结果(A1-2)
    await route.fulfill({ json: { status: "duplicate", record_id: recordId } });
    host.ws!.send(
      envelope("intent.result", {
        record_id: recordId,
        status: "success",
        title: "已新建:回复客户邮件",
      }),
    );
  });

  await page.goto("/");
  const startBtn = page.getByRole("button", { name: "开始录音" });
  await expect(startBtn).toBeVisible({ timeout: 10_000 });
  await startBtn.click();
  await expect(page.getByRole("button", { name: "点击结束" })).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(2_500);
  await page.getByRole("button", { name: "点击结束" }).click();

  // 200 受理后、结果到达前"关闭页面"(重开 = 关闭并重载,IndexedDB 凭据持久)
  await expect.poll(() => posts, { timeout: 10_000 }).toBe(1);
  await page.reload();
  await expect(page.locator(".result h1")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".result h1")).toHaveText("已新建:回复客户邮件");
  expect(posts).toBe(2); // 重开后自动补传一次(duplicate)
  await expect.poll(() => queueLength(page), { timeout: 10_000 }).toBe(0);
});

test("clarify 中间态不出队,最终确认终态到达才删除(A1-2)", async ({ page }) => {
  const host = await installFakeHost(page);
  let recordId = "";
  await page.route("**/audio/**", async (route, request) => {
    recordId = request.url().split("/").pop() ?? "";
    await route.fulfill({ json: { status: "received", record_id: recordId } });
  });

  await page.goto("/");
  const startBtn = page.getByRole("button", { name: "开始录音" });
  await expect(startBtn).toBeVisible({ timeout: 10_000 });
  await startBtn.click();
  await expect(page.getByRole("button", { name: "点击结束" })).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(2_500);
  await page.getByRole("button", { name: "点击结束" }).click();
  await expect.poll(() => recordId !== "", { timeout: 10_000 }).toBe(true);

  // 中间态 clarify 到达:展示候选,队列必须保留
  host.ws!.send(
    envelope("intent.result", {
      record_id: recordId,
      status: "clarify",
      title: "未精确匹配,请确认任务",
      candidates: [
        { candidate_id: "t-1", label: "周报撰写" },
        { candidate_id: "t-2", label: "周报汇总" },
      ],
    }),
  );
  await expect(page.getByRole("button", { name: "周报撰写" })).toBeVisible({ timeout: 10_000 });
  expect(await queueLength(page)).toBe(1);

  // 用户选定 → 最终确认终态到达后才出队
  await page.getByRole("button", { name: "周报撰写" }).click();
  host.ws!.send(
    envelope("intent.result", { record_id: recordId, status: "success", title: "已完成:周报撰写" }),
  );
  await expect(page.locator(".result h1")).toBeVisible({ timeout: 10_000 });
  await expect.poll(() => queueLength(page), { timeout: 10_000 }).toBe(0);
});

test("验收:60 秒音频在 100KB/s 限速下上传成功(FR-02)", async ({ page, context }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 20_000 });

  // 直接向 IndexedDB 预置 60 秒"音频"(≈1.87MB,时长/体积忠实于验收口径;
  // 负载故意不可解码:ASR 快进失败,避免 60s 真转写挤占后续用例——
  // FR-02 验收的是弱网上传成功率,不是转写质量)
  const recordId = "e2e-60s-slowlink";
  await page.evaluate(async (rid) => {
    const rate = 16_000;
    const seconds = 60;
    const dataSize = rate * 2 * seconds;
    const buf = new ArrayBuffer(44 + dataSize);
    const v = new DataView(buf);
    const writeStr = (off: number, s: string) => {
      for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i));
    };
    writeStr(0, "NOPE"); // 非法 RIFF:容器不可解码,转写立即失败(见上注释)
    v.setUint32(4, 36 + dataSize, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); // PCM
    v.setUint16(22, 1, true); // mono
    v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    writeStr(36, "data");
    v.setUint32(40, dataSize, true);
    const db = (await new Promise((resolve, reject) => {
      const req = indexedDB.open("vbadge", 1);
      req.onupgradeneeded = () =>
        req.result.createObjectStore("pending_audio", { keyPath: "record_id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    })) as IDBDatabase;
    await new Promise<void>((resolve, reject) => {
      const req = db
        .transaction("pending_audio", "readwrite")
        .objectStore("pending_audio")
        .put({
          record_id: rid,
          blob: new Blob([buf], { type: "audio/wav" }),
          duration_ms: 60_000,
          queued_at: Date.now(),
          fmt: "wav",
        });
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }, recordId);

  // 限速 100KB/s(FR-02 验收口径),重载触发自动补传
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: 50,
    downloadThroughput: 100 * 1024,
    uploadThroughput: 100 * 1024,
  });

  const accepted = page.waitForResponse(
    (resp) => resp.url().includes(`/audio/${recordId}`) && resp.status() === 200,
    { timeout: 90_000 },
  );
  await page.reload();
  await accepted; // ≈1.87MB / 100KB/s ≈ 19s,允许重试

  // 服务端受理并登记(转写异步进行;长静音的终态不限)
  await expect(async () => {
    const records = (await (await page.request.get("/desk/records")).json()) as {
      status: string;
    }[];
    expect(records.length).toBeGreaterThan(0);
  }).toPass({ timeout: 120_000 });
});
