/**
 * e2e 数据重置:在隔离运行目录(frontend/.e2e-runtime)内执行 mock import,
 * 只影响独立测试服务的数据库,不碰仓库根 data/agent.db(真实数据)。
 */
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RUNTIME_DIR = path.resolve(HERE, "../.e2e-runtime");
const AGENT_HOST = path.resolve(HERE, "../../backend/.venv/bin/agent-host");

/** 重置隔离库的演示数据(任务/日历/卡片/简报) */
export function reseed(): void {
  execSync(`"${AGENT_HOST}" mock import`, { cwd: RUNTIME_DIR, stdio: "inherit" });
}
