"""FR-05 待办转任务草稿(方案 D):笔记确认归档后,"## 待办"有效条目入 task_drafts 只读展示。

边界(Owner 决策):任务草稿不进 tasks 表;只解析固定四段模板的"## 待办"章节,
跳过占位行;UNIQUE(source_draft_id, title) + INSERT OR IGNORE 幂等;
不实现确认/放弃/编辑/删除。全部运行时数据隔离在 tmp_path,不碰本机 data/agent.db。
"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.api.app import create_app
from agent_host.config import AppConfig, StoreConfig
from agent_host.skills.field_note import extract_todo_titles

TODO_CONTENT = (
    "# 现场记录\n\n"
    "## 背景\n讨论下季度方案。\n\n"
    "## 要点\n- 方案需要评审\n- 预算待确认\n\n"
    "## 结论\n(待人工补充)\n\n"
    "## 待办\n- 需要整理会议纪要\n- 记得跟进预算审批\n"
)
NO_TODO_CONTENT = (
    "# 现场记录\n\n"
    "## 背景\n随便聊聊。\n\n"
    "## 要点\n- 随便聊聊。\n\n"
    "## 结论\n(待人工补充)\n\n"
    "## 待办\n- (无明确待办)\n"
)


@pytest.fixture()
def env(tmp_path: Path) -> tuple[TestClient, Path]:
    """隔离环境:tmp 数据库 + tmp 归档目录(mock ASR,不经音频管线)。"""
    db_path = tmp_path / "agent.db"
    config = AppConfig(
        store=StoreConfig(db_path=str(db_path), notes_dir=str(tmp_path / "notes")),
    )
    return TestClient(create_app(config)), db_path


def _seed_note_draft(db_path: Path, draft_id: str, content_md: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO records (id, device_id, source, mode, started_at, duration_ms,"
        " status, created_at)"
        " VALUES (?, NULL, 'pc_text', 'field', '2026-07-29T00:00:00+00:00', 0, 'done',"
        " '2026-07-29T00:00:00+00:00')",
        (f"rec-{draft_id}",),
    )
    conn.execute(
        "INSERT INTO drafts (id, record_id, kind, content_md, status, created_at)"
        " VALUES (?, ?, 'note', ?, 'pending', '2026-07-29T00:01:00+00:00')",
        (draft_id, f"rec-{draft_id}", content_md),
    )
    conn.commit()
    conn.close()


def _rows(db_path: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # table 为测试常量
    conn.close()
    return rows


def test_fr05_extract_only_todo_section() -> None:
    """只取"## 待办"章节 bullets:要点的"需要评审"不得混入;占位行跳过。"""
    assert extract_todo_titles(TODO_CONTENT) == ["需要整理会议纪要", "记得跟进预算审批"]
    assert extract_todo_titles(NO_TODO_CONTENT) == []
    assert extract_todo_titles("# 现场记录\n\n## 背景\n无待办章节。\n") == []


def test_fr05_archive_creates_task_drafts_not_tasks(
    env: tuple[TestClient, Path],
) -> None:
    """确认归档后:两条有效待办 → 两条任务草稿;tasks 表保持为空。"""
    client, db_path = env
    _seed_note_draft(db_path, "draft-td-1", TODO_CONTENT)

    assert client.post("/desk/drafts/draft-td-1/confirm").status_code == 200

    task_drafts = _rows(db_path, "task_drafts")
    assert [r["title"] for r in task_drafts] == ["需要整理会议纪要", "记得跟进预算审批"]
    assert all(r["source_draft_id"] == "draft-td-1" for r in task_drafts)
    assert _rows(db_path, "tasks") == []  # 任务草稿不进正式 tasks 表

    # /desk/drafts 合并可见:kind=task、content_md=标题、status=pending
    desk = client.get("/desk/drafts").json()
    mine = [d for d in desk if d["kind"] == "task"]
    assert [d["content_md"] for d in mine] == ["需要整理会议纪要", "记得跟进预算审批"]
    assert all(d["status"] == "pending" for d in mine)


def test_fr05_archive_no_todo_creates_zero_task_drafts(
    env: tuple[TestClient, Path],
) -> None:
    """无明确待办的笔记:确认归档正常,任务草稿 0 条。"""
    client, db_path = env
    _seed_note_draft(db_path, "draft-td-2", NO_TODO_CONTENT)

    assert client.post("/desk/drafts/draft-td-2/confirm").status_code == 200
    assert _rows(db_path, "task_drafts") == []
    assert all(d["kind"] != "task" for d in client.get("/desk/drafts").json())


def test_fr05_task_drafts_idempotent_for_same_note(tmp_path: Path) -> None:
    """同一笔记重复保存不重复生成(UNIQUE + INSERT OR IGNORE)。"""
    from agent_host.store.db import init_db
    from agent_host.store.repos import TaskDraftRepo

    conn = init_db(str(tmp_path / "agent.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO drafts (id, record_id, kind, content_md, status, created_at)"
        " VALUES ('src-1', NULL, 'note', 'x', 'confirmed', '2026-07-29T00:00:00+00:00')"
    )
    conn.commit()
    repo = TaskDraftRepo(conn)

    titles = ["需要整理会议纪要", "记得跟进预算审批"]
    repo.create_many("src-1", titles)
    repo.create_many("src-1", titles)  # 重复保存
    rows = repo.list_all()
    assert [r["title"] for r in rows] == titles  # 仍是两条,不翻倍


def test_fr05_task_drafts_failure_rolls_back_and_retry_succeeds(tmp_path: Path) -> None:
    """故障注入:trigger 强制 task_drafts INSERT 失败 → 首次 500 且整体回滚;移除故障重试成功。

    回滚断言:drafts 仍 pending、task_drafts 为 0、无成功审计;重试后三者均正确,
    成功审计只有一条。
    """
    db_path = tmp_path / "agent.db"
    config = AppConfig(
        store=StoreConfig(db_path=str(db_path), notes_dir=str(tmp_path / "notes"))
    )
    # raise_server_exceptions=False:未捕获异常以 500 响应呈现(与生产 FastAPI 行为一致)
    client = TestClient(create_app(config), raise_server_exceptions=False)
    _seed_note_draft(db_path, "draft-td-fail", TODO_CONTENT)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TRIGGER fail_task_drafts BEFORE INSERT ON task_drafts"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    conn.commit()
    conn.close()

    first = client.post("/desk/drafts/draft-td-fail/confirm")
    assert first.status_code == 500
    assert _rows(db_path, "drafts")[0]["status"] == "pending"  # 状态转换被回滚
    assert _rows(db_path, "task_drafts") == []  # 不留部分数据
    assert _rows(db_path, "audit_log") == []  # 不写成功审计

    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TRIGGER fail_task_drafts")
    conn.commit()
    conn.close()

    second = client.post("/desk/drafts/draft-td-fail/confirm")
    assert second.status_code == 200
    assert _rows(db_path, "drafts")[0]["status"] == "confirmed"
    assert [r["title"] for r in _rows(db_path, "task_drafts")] == [
        "需要整理会议纪要",
        "记得跟进预算审批",
    ]
    audits = _rows(db_path, "audit_log")
    assert len(audits) == 1
    assert audits[0]["decision"] == "confirmed"


def test_fr05_task_drafts_beyond_50_all_visible(tmp_path: Path) -> None:
    """回归:list_all 不截断——51 条任务草稿经 /desk/drafts 全部可见,最新一条不被隐藏。"""
    from agent_host.store.db import init_db
    from agent_host.store.repos import TaskDraftRepo

    db_path = tmp_path / "agent.db"
    conn = init_db(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO drafts (id, record_id, kind, content_md, status, created_at)"
        " VALUES ('src-51', NULL, 'note', 'x', 'confirmed', '2026-07-29T00:00:00+00:00')"
    )
    conn.commit()
    titles = [f"待办事项 {i:02d}" for i in range(1, 52)]
    TaskDraftRepo(conn).create_many("src-51", titles)
    conn.close()

    config = AppConfig(
        store=StoreConfig(db_path=str(db_path), notes_dir=str(tmp_path / "notes"))
    )
    client = TestClient(create_app(config))
    desk = client.get("/desk/drafts").json()
    mine = [d["content_md"] for d in desk if d["kind"] == "task"]
    assert len(mine) == 51
    assert "待办事项 51" in mine  # 最新一条可见,不被最早 50 条窗口隐藏
