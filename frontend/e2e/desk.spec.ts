/**
 * PC 草稿工作台(?desk=1):入口渲染与文案合规。
 * 本用例只读(GET /desk/*),不写入任何运行时数据;
 * 数据内容由真实链路验证(backend tests/test_fr05_field_note_desk.py)。
 */
import { expect, test } from "@playwright/test";

test("草稿工作台:入口渲染,且无内部术语", async ({ page }) => {
  await page.goto("/?desk=1");
  await expect(page.getByRole("heading", { name: "草稿工作台" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近处理记录" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "待办任务 / 提醒" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "待确认草稿" })).toBeVisible();
  // 归档未实现:界面不得出现可归档的承诺
  await expect(page.getByText(/确认归档|归档/)).toHaveCount(1); // 仅"归档功能尚未实现"一句
  // 内部术语不得出现在用户界面
  const body = await page.locator("body").innerText();
  for (const term of ["宪法", "FR-", "Mock", "规约", "error_code", "A0", "第 8 条"]) {
    expect(body).not.toContain(term);
  }
});

test("草稿工作台:不影响工牌入口与 eink 档", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 20_000 });

  const einkPage = await page.context().newPage();
  await einkPage.goto("/?eink=a");
  await expect(einkPage.locator(".eink-canvas")).toBeVisible({ timeout: 20_000 });
});
