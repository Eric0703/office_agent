"""命令行:启动/配对/日志导出/Mock 数据导入(FR-13;登记册 §2.1 配对 CLI)。"""

import argparse
from datetime import datetime

from agent_host.config import load_config


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器(agent-host <cmd>)。"""
    parser = argparse.ArgumentParser(prog="agent-host", description="AI 工牌 Agent 主机 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="启动 HTTP/WS 服务")

    pair = sub.add_parser("pair", help="设备配对管理")
    pair_sub = pair.add_subparsers(dest="pair_command", required=True)
    approve = pair_sub.add_parser("approve", help="批准配对码(配对码 5 分钟有效、一次性)")
    approve.add_argument("pair_code", help="端侧展示的 6 位数字配对码")
    revoke = pair_sub.add_parser("revoke", help="吊销设备(token 立即失效)")
    revoke.add_argument("device_id", help="配对时分配的设备 id")

    mock = sub.add_parser("mock", help="Mock 数据管理")
    mock_sub = mock.add_subparsers(dest="mock_command", required=True)
    mock_sub.add_parser("import", help="导入 Mock 任务/日历/提醒卡片数据")

    return parser


def _mock_import() -> int:
    """预置原型演示数据(幂等,可重复执行):3 任务 + 2 日历事件 + 2 卡片 + 1 简报。"""
    import json
    from datetime import UTC

    from agent_host.store.db import init_db
    from agent_host.store.repos import BriefingRepo, CalendarEventRepo, CardRepo, TaskRepo

    config = load_config("config.yaml")
    conn = init_db(config.store.db_path)
    tasks = TaskRepo(conn)
    events = CalendarEventRepo(conn)
    cards = CardRepo(conn)
    briefings = BriefingRepo(conn)

    weekly = tasks.insert("周报撰写", task_id="task-weekly-report")
    tasks.insert("周报汇总", task_id="task-weekly-collect")  # 与"周报撰写"构成同名歧义演示
    tasks.insert("数据库迁移", task_id="task-db-migration")

    today = datetime.now().astimezone()
    evt1 = today.replace(hour=10, minute=0, second=0, microsecond=0)
    evt2 = today.replace(hour=16, minute=0, second=0, microsecond=0)
    remind = today.replace(hour=18, minute=0, second=0, microsecond=0)
    events.insert(
        "evt-standup", "项目例会", evt1.isoformat(), evt1.replace(hour=10, minute=30).isoformat()
    )
    events.insert(
        "evt-review", "客户方案评审", evt2.isoformat(), evt2.replace(hour=17, minute=0).isoformat()
    )

    cards.upsert(
        "card-weekly-report",
        kind="task",
        title="周报撰写截止",
        body="今天 18:00 前提交",
        remind_at=remind.isoformat(),
        ref_task_id=weekly,
    )
    cards.upsert(
        "card-review",
        kind="timer",
        title="客户方案评审",
        body="16:00 开始,提前 10 分钟接入",
        remind_at=evt2.isoformat(),
    )

    # 当日简报:条目含来源 task/event id,可追溯(FR-06);≤5 条(登记册 §2.4)
    items = [
        {"kind": "event", "title": "项目例会", "time": "10:00", "source_id": "evt-standup"},
        {"kind": "event", "title": "客户方案评审", "time": "16:00", "source_id": "evt-review"},
        {"kind": "task", "title": "周报撰写", "time": None, "source_id": weekly},
    ]
    briefings.save(
        today.date().isoformat(),
        json.dumps(items, ensure_ascii=False),
        datetime.now(UTC).isoformat(),
    )

    print("Mock 数据已导入:")
    print("  任务 ×3:周报撰写 / 周报汇总 / 数据库迁移")
    print(f"  日历 ×2:项目例会 {evt1.isoformat(timespec='minutes')} / 客户方案评审 "
          f"{evt2.isoformat(timespec='minutes')}")
    print("  卡片 ×2:card-weekly-report(任务)/ card-review(定时)")
    print(f"  简报 ×1:{today.date().isoformat()},{len(items)} 条")
    print(f"  数据库:{config.store.db_path}")
    return 0


def _pair_command(pair_command: str, target: str) -> int:
    """配对管理:经本机 desk 接口作用于正在运行的服务(单一事实源,免跨进程状态)。

    approve:批准端侧展示的 6 位配对码;revoke:吊销设备,token 立即失效(FR-01)。
    """
    import json
    import urllib.error
    import urllib.request

    config = load_config("config.yaml")
    base = f"http://127.0.0.1:{config.server.port}"
    path = "/desk/pair/approve" if pair_command == "approve" else "/desk/pair/revoke"
    field = "code" if pair_command == "approve" else "device_id"
    req = urllib.request.Request(
        base + path,
        data=json.dumps({field: target}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"pair {pair_command} {target}:{resp.read().decode()}")
            return 0
    except urllib.error.HTTPError as exc:
        print(f"pair {pair_command} {target} 失败:{exc.read().decode()}")
        return 1
    except urllib.error.URLError:
        print(f"无法连接本机服务({base}),请先启动 agent-host serve")
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        from agent_host.main import main as serve_main

        serve_main()
        return 0

    if args.command == "pair":
        target = args.pair_code if args.pair_command == "approve" else args.device_id
        return _pair_command(args.pair_command, target)

    if args.command == "mock":
        return _mock_import()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
