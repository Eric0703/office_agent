/**
 * PC 草稿工作台(?desk=1):入口渲染、文案合规与笔记草稿人工确认归档(FR-05)。
 * 运行在隔离测试服务(frontend/.e2e-runtime,端口 8100);草稿种子直接写入隔离库,
 * 归档文件落在 .e2e-runtime/data/notes,均不触碰仓库根 data/agent.db(真实数据)。
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RUNTIME_DIR = path.resolve(HERE, "../.e2e-runtime");
const PY = path.resolve(HERE, "../../backend/.venv/bin/python");

const DRAFT_CONTENT = "# 现场记录\n\n## 背景\n讨论下季度方案。\n";

/** 向隔离库直接落一条 pending 笔记草稿(含关联 record) */
function seedNoteDraft(draftId: string): void {
  const code = `
import sqlite3
conn = sqlite3.connect("data/agent.db")
conn.execute("PRAGMA foreign_keys = ON")
conn.execute(
    "INSERT OR IGNORE INTO records (id, device_id, source, mode, started_at, duration_ms, status, created_at)"
    " VALUES (?, NULL, 'pc_text', 'field', '2026-07-29T00:00:00+00:00', 0, 'done', '2026-07-29T00:00:00+00:00')",
    ("rec-e2e-${draftId}",),
)
conn.execute(
    "INSERT OR REPLACE INTO drafts (id, record_id, kind, content_md, status, created_at)"
    " VALUES (?, ?, 'note', ?, 'pending', '2026-07-29T00:01:00+00:00')",
    ("${draftId}", "rec-e2e-${draftId}", ${JSON.stringify(DRAFT_CONTENT)}),
)
conn.commit()
conn.close()
`;
  execFileSync(PY, ["-c", code], { cwd: RUNTIME_DIR });
}

test("草稿工作台:入口渲染,且无内部术语", async ({ page }) => {
  await page.goto("/?desk=1");
  await expect(page.getByRole("heading", { name: "草稿工作台" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近处理记录" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "待办任务 / 提醒" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "待确认草稿" })).toBeVisible();
  // 无待确认草稿时,"归档"仅出现在说明文案一句;不得声称待办可转任务草稿
  await expect(page.getByText(/归档/)).toHaveCount(1);
  // 内部术语不得出现在用户界面
  const body = await page.locator("body").innerText();
  for (const term of ["宪法", "FR-", "Mock", "规约", "error_code", "A0", "第 8 条"]) {
    expect(body).not.toContain(term);
  }
});

test("草稿工作台:笔记草稿确认归档后从待确认区消失,且不显示文件路径", async ({ page }) => {
  seedNoteDraft("e2e-draft-confirm");
  await page.goto("/?desk=1");
  const button = page.getByRole("button", { name: "确认归档" });
  await expect(button).toBeVisible();
  await button.click();
  await expect(page.getByText("草稿已归档到本机笔记目录")).toBeVisible();
  // 草稿从待确认区消失
  await expect(button).toHaveCount(0);
  await expect(page.getByText("讨论下季度方案。")).toHaveCount(0);
  // 不向用户展示本机文件路径
  const body = await page.locator("body").innerText();
  expect(body).not.toContain("data/notes");
  expect(body).not.toContain(".md");
});

test("草稿工作台:归档失败给出简短提示,不暴露内部细节", async ({ page }) => {
  seedNoteDraft("e2e-draft-fail");
  await page.route("**/desk/drafts/*/confirm", (route) =>
    route.fulfill({ status: 500, body: "boom" }),
  );
  await page.goto("/?desk=1");
  await page.getByRole("button", { name: "确认归档" }).click();
  await expect(page.getByText("归档未成功,请稍后重试")).toBeVisible();
  // 草稿仍在待确认区;界面无异常/堆栈/内部编号
  await expect(page.getByRole("button", { name: "确认归档" })).toBeVisible();
  const body = await page.locator("body").innerText();
  expect(body).not.toContain("boom");
  expect(body).not.toContain("500");
});

test("草稿工作台:不影响工牌入口与 eink 档", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "开始录音" })).toBeVisible({ timeout: 20_000 });

  const einkPage = await page.context().newPage();
  await einkPage.goto("/?eink=a");
  await expect(einkPage.locator(".eink-canvas")).toBeVisible({ timeout: 20_000 });
});
