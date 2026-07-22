/**
 * P1 时序回归(08 §1.1 时序等价):WS intent.result 先于 HTTP 200 到达(如 duplicate 补推)。
 * 确定性构造:上传请求发出后先经假 WS 推送结果,≥500ms 后才 fulfill HTTP 200。
 * 断言:两种顺序收敛同一终态——结果/候选可见,不停在 processing;
 * 终态凭据出队,clarify 中间态保留凭据。
 */
import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

import { envelope, queueLength } from "./seed";

interface OrderedHost {
  recordId: string;
  ws: WebSocketRoute | null;
  httpDone: boolean;
}

/** 假主机:hello ok + state.sync;/audio 收到请求后先推 firstResult,600ms 后回 200 */
async function installOrderedHost(
  page: Page,
  firstResult: Record<string, unknown>,
): Promise<OrderedHost> {
  const host: OrderedHost = { recordId: "", ws: null, httpDone: false };
  await page.routeWebSocket("**/ws", (ws) => {
    host.ws = ws;
    ws.onMessage((msg) => {
      const m = JSON.parse(String(msg)) as { type: string };
      if (m.type === "device.hello") {
        ws.send(
          envelope("device.hello.result", {
            status: "ok",
            server_time: Date.now(),
            device_id: "dev-fake",
          }),
        );
        ws.send(envelope("state.sync", { cards: [] }));
      }
    });
  });
  await page.route("**/audio/**", async (route, request) => {
    host.recordId = request.url().split("/").pop() ?? "";
    // P1 时序:WS intent.result 先于 HTTP 200(凭据此时已入队)
    host.ws!.send(envelope("intent.result", { record_id: host.recordId, ...firstResult }));
    await new Promise((resolve) => setTimeout(resolve, 600));
    host.httpDone = true;
    await route.fulfill({ json: { status: "received", record_id: host.recordId } });
  });
  return host;
}

/** 真实录一段 2.5s(虚拟麦克风),触发上传 */
async function recordShortUtterance(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "开始录音" }).click();
  await expect(page.getByRole("button", { name: "点击结束" })).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(2_500);
  await page.getByRole("button", { name: "点击结束" }).click();
}

test("终态先到:结果页在 HTTP 200 前可见,不停 processing,凭据终态出队", async ({ page }) => {
  const host = await installOrderedHost(page, {
    status: "success",
    title: "已新建:回复客户邮件",
  });
  await recordShortUtterance(page);

  // 结果页必须在 HTTP 200 返回之前已可见(WS 先到直出)
  await expect(page.locator(".result h1")).toBeVisible({ timeout: 10_000 });
  expect(host.httpDone).toBe(false);
  await expect(page.getByText("识别处理中…")).toHaveCount(0);

  // 成功结果 3s 自动返回原页面:不得卡在 processing;恢复凭据终态出队
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("识别处理中…")).toHaveCount(0);
  await expect.poll(() => queueLength(page), { timeout: 10_000 }).toBe(0);
});

test("clarify 先到:候选在 HTTP 200 前可见且凭据保留,最终终态到达才出队", async ({ page }) => {
  const host = await installOrderedHost(page, {
    status: "clarify",
    title: "未精确匹配,请确认任务",
    candidates: [
      { candidate_id: "t-1", label: "周报撰写" },
      { candidate_id: "t-2", label: "周报汇总" },
    ],
  });
  await recordShortUtterance(page);

  // 候选必须在 HTTP 200 之前可见;clarify 是中间态,凭据保留
  await expect(page.getByRole("button", { name: "周报撰写" })).toBeVisible({ timeout: 10_000 });
  expect(host.httpDone).toBe(false);
  expect(await queueLength(page)).toBe(1);

  // 晚到的 HTTP 200 不得覆盖 clarify;用户选定后,最终终态到达才出队
  await expect.poll(() => host.httpDone, { timeout: 10_000 }).toBe(true);
  await expect(page.getByRole("button", { name: "周报撰写" })).toBeVisible();
  await page.getByRole("button", { name: "周报撰写" }).click();
  host.ws!.send(
    envelope("intent.result", {
      record_id: host.recordId,
      status: "success",
      title: "已完成:周报撰写",
    }),
  );
  await expect(page.locator(".result h1")).toHaveText("已完成:周报撰写", { timeout: 10_000 });
  await expect.poll(() => queueLength(page), { timeout: 10_000 }).toBe(0);
});
