/**
 * PWA 待办(问题 1/2 回归):顶对齐布局 + checkbox 勾选完成/取消并同步。
 * 勾选后卡片经服务端广播撤下,/desk/tasks 状态同步更新。
 * 多任务可编辑预览的链路验证在后端 test_fr08_multi_task.py(不伪造 WS 帧)。
 * 数据在隔离测试服务(8100 端口 + frontend/.e2e-runtime)内重置,不碰真实数据库。
 */
import { expect, test } from "@playwright/test";

import { reseed } from "./seed";

test.beforeAll(() => {
  // 重置演示数据:任务卡"周报撰写截止" + timer 卡"客户方案评审"(16:00 最近优先)
  reseed();
});

test("PWA 待办:顶对齐 + 勾选完成任务/取消提醒并同步", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 20_000 });

  // 真实数据库会随体验累积卡片,只断言目标卡存在、不锁死总数
  const timerCard = page.locator(".cards li", { hasText: "客户方案评审" });
  const taskCard = page.locator(".cards li", { hasText: "周报撰写截止" });
  await expect(timerCard).toHaveCount(1);
  await expect(taskCard).toHaveCount(1);

  // 顶对齐:标题贴近页面顶部、首张卡中心在上半屏,不再垂直居中(投影页回归)
  const box = await page.locator(".cards li").first().boundingBox();
  expect(box).not.toBeNull();
  expect(box!.y + box!.height / 2).toBeLessThan(360);
  const h1Box = await page.getByRole("heading", { name: "今日" }).boundingBox();
  expect(h1Box!.y).toBeLessThan(80);

  // timer 卡勾选 = 取消提醒:卡片消失,/desk/tasks 显示已撤下
  await timerCard.getByRole("checkbox").check();
  await expect(timerCard).toHaveCount(0);
  const afterCancel = (await (await page.request.get("/desk/tasks")).json()) as {
    title: string;
    status: string;
  }[];
  expect(afterCancel.find((t) => t.title === "客户方案评审")?.status).toBe("已撤下");

  // 任务卡勾选 = 完成任务:卡片消失,desk 显示已完成
  await taskCard.getByRole("checkbox").check();
  await expect(taskCard).toHaveCount(0);
  const afterDone = (await (await page.request.get("/desk/tasks")).json()) as {
    title: string;
    status: string;
  }[];
  expect(afterDone.find((t) => t.title === "周报撰写")?.status).toBe("已完成");
});
