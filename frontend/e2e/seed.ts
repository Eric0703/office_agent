/**
 * e2e 数据重置:在隔离运行目录(frontend/.e2e-runtime)内执行 mock import,
 * 只影响独立测试服务的数据库,不碰仓库根 data/agent.db(真实数据)。
 */
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { Page, WebSocketRoute } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RUNTIME_DIR = path.resolve(HERE, "../.e2e-runtime");
const AGENT_HOST = path.resolve(HERE, "../../backend/.venv/bin/agent-host");

/** 重置隔离库的演示数据(任务/日历/卡片/简报) */
export function reseed(): void {
  execSync(`"${AGENT_HOST}" mock import`, { cwd: RUNTIME_DIR, stdio: "inherit" });
}

/** 构造协议信封(假 WS 主机用) */
export function envelope(type: string, payload: object): string {
  return JSON.stringify({
    type,
    version: "1.0",
    id: crypto.randomUUID(),
    ts: Date.now(),
    payload,
  });
}

export interface FakeHost {
  ws: WebSocketRoute | null;
  hellos: number;
  /** 收到的 device.hello payload(按次序;capabilities 等断言用) */
  helloPayloads: Array<Record<string, unknown>>;
  /** 收到的全部非 hello D→H 消息(clarify.select 等断言用) */
  messages: Array<{ type: string; payload?: Record<string, unknown> }>;
}

/** 装配假主机(hello ok + state.sync;其余消息记录到 messages),用于无真实 ASR 依赖的确定性用例。
 *  seed.cards/briefing 作为 state.sync 内容下发(页模型用例需要种子卡/简报)。 */
export async function installFakeHost(
  page: Page,
  seed: { cards?: object[]; briefing?: object } = {},
): Promise<FakeHost> {
  const host: FakeHost = { ws: null, hellos: 0, helloPayloads: [], messages: [] };
  await page.routeWebSocket("**/ws", (ws) => {
    host.ws = ws;
    ws.onMessage((msg) => {
      const m = JSON.parse(String(msg)) as { type: string; payload?: Record<string, unknown> };
      if (m.type === "device.hello") {
        host.hellos += 1;
        host.helloPayloads.push(m.payload ?? {});
        ws.send(
          envelope("device.hello.result", {
            status: "ok",
            server_time: Date.now(),
            device_id: "dev-fake",
          }),
        );
        ws.send(
          envelope("state.sync", {
            cards: seed.cards ?? [],
            ...(seed.briefing ? { briefing: seed.briefing } : {}),
          }),
        );
      } else {
        host.messages.push(m);
      }
    });
  });
  return host;
}

/** 直接向 IndexedDB 写入一条待补传音频(内容占位;HTTP 由路由拦截时无需真实音频) */
export async function seedPendingAudio(page: Page, recordId: string): Promise<void> {
  await page.evaluate(async (rid) => {
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
          blob: new Blob(["e2e"], { type: "audio/webm" }),
          duration_ms: 2500,
          queued_at: Date.now(),
          fmt: "webm-opus",
        });
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }, recordId);
}

/** 读取离线队列长度(IndexedDB pending_audio) */
export async function queueLength(page: Page): Promise<number> {
  return page.evaluate(async () => {
    const db = (await new Promise((resolve, reject) => {
      const req = indexedDB.open("vbadge", 1);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    })) as IDBDatabase;
    return (await new Promise((resolve, reject) => {
      const req = db.transaction("pending_audio", "readonly").objectStore("pending_audio").getAll();
      req.onsuccess = () => resolve((req.result as unknown[]).length);
      req.onerror = () => reject(req.error);
    })) as number;
  });
}
