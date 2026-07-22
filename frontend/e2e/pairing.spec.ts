/**
 * A1-1 配对与心跳(假 WS + 假时钟,不依赖后端行为):
 * - 配对码 expired 后自动生成并提交新码(不停留在无码等待态);
 * - 心跳同 id 校验,连续 2 次未达主动断连重连,恢复后重新认证并收到 state.sync。
 */
import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

interface FakeHost {
  hellos: number;
  syncs: number;
  beats: number;
  pairRequests: string[];
  answerPongs: boolean;
  ws: WebSocketRoute | null;
}

function envelope(type: string, payload: object): string {
  return JSON.stringify({
    type,
    version: "1.0",
    id: crypto.randomUUID(),
    ts: Date.now(),
    payload,
  });
}

/** 装配假主机:hello 一律 ok + state.sync;heartbeat 可控制是否回应同 id pong */
async function installFakeHost(page: Page): Promise<FakeHost> {
  const host: FakeHost = {
    hellos: 0,
    syncs: 0,
    beats: 0,
    pairRequests: [],
    answerPongs: true,
    ws: null,
  };
  await page.routeWebSocket("**/ws", (ws) => {
    host.ws = ws;
    ws.onMessage((msg) => {
      const m = JSON.parse(String(msg)) as { type: string; payload?: { pair_code?: string } };
      if (m.type === "device.hello") {
        host.hellos += 1;
        ws.send(
          envelope("device.hello.result", {
            status: "ok",
            server_time: Date.now(),
            device_id: "dev-fake",
          }),
        );
        ws.send(envelope("state.sync", { cards: [] }));
        host.syncs += 1;
      } else if (m.type === "heartbeat") {
        host.beats += 1;
        if (host.answerPongs) {
          ws.send(String(msg)); // pong 复用同一信封 id(登记册 §2.1)
        }
      } else if (m.type === "device.pair.request") {
        host.pairRequests.push(m.payload?.pair_code ?? "");
      }
    });
  });
  return host;
}

test("心跳:两次未达后主动断连重连,恢复后重新认证并收到 state.sync", async ({ page }) => {
  await page.clock.install();
  const host = await installFakeHost(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 10_000 });
  expect(host.hellos).toBe(1);
  expect(host.syncs).toBe(1);

  // 第一次心跳:正常回应,链路健康
  await page.clock.fastForward(31_000);
  expect(host.beats).toBe(1);

  // 之后不再回应:第 2 次未达记 1 次,第 3 次未达触发主动断连
  host.answerPongs = false;
  await page.clock.fastForward(31_000); // beat 2 发出,pending
  await page.clock.fastForward(31_000); // beat 3:未达 1 次,再发
  await page.clock.fastForward(31_000); // beat 4:未达 2 次 → 断连
  await page.clock.fastForward(3_000); // 退避重连计时
  await expect
    .poll(() => host.hellos, { timeout: 10_000 })
    .toBeGreaterThanOrEqual(2);
  expect(host.syncs).toBeGreaterThanOrEqual(2); // 重连后重新认证并收到 state.sync
});

test("配对码 expired:自动生成新配对码并重新提交 pair.request", async ({ page }) => {
  const pairCodes: string[] = [];
  let hostWs: WebSocketRoute | null = null;
  await page.routeWebSocket("**/ws", (ws) => {
    hostWs = ws;
    ws.onMessage((msg) => {
      const m = JSON.parse(String(msg)) as { type: string; payload?: { pair_code?: string } };
      if (m.type === "device.hello") {
        ws.send(envelope("device.hello.result", { status: "pair_required", server_time: Date.now() }));
        ws.close(); // 登记册 §2.1:status != ok 时主机发送结果后关闭
      } else if (m.type === "device.pair.request") {
        pairCodes.push(m.payload?.pair_code ?? "");
      }
    });
  });

  await page.goto("/");
  // 未配对:展示配对码并提交 pair.request
  await expect(page.locator(".paircode")).toBeVisible({ timeout: 10_000 });
  await expect.poll(() => pairCodes.length, { timeout: 10_000 }).toBe(1);
  const firstCode = pairCodes[0];
  expect(firstCode).toMatch(/^\d{6}$/);
  await expect(page.locator(".paircode")).toHaveText(firstCode);

  // 主机按 expired 处理:端侧应立即生成新码并重新提交,不卡住
  hostWs!.send(envelope("device.pair.result", { status: "expired" }));
  await expect.poll(() => pairCodes.length, { timeout: 10_000 }).toBe(2);
  expect(pairCodes[1]).toMatch(/^\d{6}$/);
  expect(pairCodes[1]).not.toBe(firstCode);
  await expect(page.locator(".paircode")).toHaveText(pairCodes[1]);
});

/* ---------- 重连竞态回归(假 WS + 假时钟):连接切换只建立一个新连接 ---------- */

/** 装配带连接计数的假主机;hello 按 token 区分:带 "tok-1" 才 ok,否则 pair_required */
async function installRacyHost(page: Page): Promise<{
  open: () => number;
  total: () => number;
  pairRequests: string[];
  hellos: number;
  ws: () => WebSocketRoute | null;
}> {
  const state = { openCount: 0, totalCount: 0, hellos: 0 };
  const pairRequests: string[] = [];
  let current: WebSocketRoute | null = null;
  await page.routeWebSocket("**/ws", (ws) => {
    state.openCount += 1;
    state.totalCount += 1;
    current = ws;
    ws.onClose(() => {
      state.openCount -= 1;
    });
    ws.onMessage((msg) => {
      const m = JSON.parse(String(msg)) as {
        type: string;
        payload?: { pair_code?: string; token?: string };
      };
      if (m.type === "device.hello") {
        state.hellos += 1;
        if (m.payload?.token === "tok-1") {
          ws.send(
            envelope("device.hello.result", {
              status: "ok",
              server_time: Date.now(),
              device_id: "dev-fake",
            }),
          );
          ws.send(envelope("state.sync", { cards: [] }));
        } else {
          ws.send(
            envelope("device.hello.result", { status: "pair_required", server_time: Date.now() }),
          );
          ws.close();
        }
      } else if (m.type === "device.pair.request") {
        pairRequests.push(m.payload?.pair_code ?? "");
      }
    });
  });
  return {
    open: () => state.openCount,
    total: () => state.totalCount,
    pairRequests,
    get hellos() {
      return state.hellos;
    },
    ws: () => current,
  };
}

test("竞态:批准后超过退避时间仍只有一个活动连接,pair.request 不重复", async ({ page }) => {
  await page.clock.install();
  const host = await installRacyHost(page);
  await page.goto("/");

  await expect(page.locator(".paircode")).toBeVisible({ timeout: 10_000 });
  await page.clock.fastForward(3_000); // 让 pair_required 关闭后的重连发生
  await expect.poll(() => host.pairRequests.length, { timeout: 10_000 }).toBe(1);

  // 批准:端侧静默关闭旧连接并重建走 hello 认证;旧 onclose 不得再触发重连
  host.ws()!.send(
    envelope("device.pair.result", { status: "approved", device_id: "dev-fake", token: "tok-1" }),
  );
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 10_000 });
  expect(host.hellos).toBe(2); // pair_required hello + 批准后带 token hello

  // 等待远超过退避时间(1s/2s/4s/8s):不得出现遗留重连或重复连接/重复 pair.request
  await page.clock.fastForward(30_000);
  expect(host.open()).toBe(1);
  expect(host.total()).toBe(3); // hello(pair_required) + pair.request + hello(token)
  expect(host.pairRequests).toHaveLength(1);
});

test("竞态:连续 expired 仍只有一个活动连接,每轮只提交一次 pair.request", async ({ page }) => {
  await page.clock.install();
  const host = await installRacyHost(page);
  await page.goto("/");

  await expect(page.locator(".paircode")).toBeVisible({ timeout: 10_000 });
  await page.clock.fastForward(3_000);
  await expect.poll(() => host.pairRequests.length, { timeout: 10_000 }).toBe(1);

  // 连续两次 expired:每次都应"静默关闭 + 一个新连接 + 一次 pair.request"
  host.ws()!.send(envelope("device.pair.result", { status: "expired" }));
  await expect.poll(() => host.pairRequests.length, { timeout: 10_000 }).toBe(2);
  host.ws()!.send(envelope("device.pair.result", { status: "rejected" }));
  await expect.poll(() => host.pairRequests.length, { timeout: 10_000 }).toBe(3);

  // 等待远超过退避时间:仍只有一个活动连接,无重复提交
  await page.clock.fastForward(30_000);
  expect(host.open()).toBe(1);
  expect(host.total()).toBe(4); // hello + 3 × pair.request 连接
  expect(new Set(host.pairRequests).size).toBe(3); // 每次都是新生成的配对码
});
