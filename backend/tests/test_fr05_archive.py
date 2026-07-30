"""FR-05 人工确认归档(A1-4 第一小步):pending 笔记草稿 → 本机 Markdown 落盘 + 状态/审计闭环。

范围边界(任务卡):仅 kind='note' 且 pending 可确认;重复确认/经验草稿 409,未知 id 404;
归档文件名只用日期与系统 draft id,不含用户正文;审计不写转写文本/草稿正文。
全部运行时数据隔离在 tmp_path,不触碰本机 config.yaml 与 data/agent.db。
"""

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.api.app import create_app
from agent_host.config import AppConfig, StoreConfig
from agent_host.store.repos import DraftRepo

DRAFT_CONTENT = "# 现场记录\n\n## 背景\n讨论下季度方案。\n\n## 要点\n- 待定\n"


@pytest.fixture()
def env(tmp_path: Path) -> tuple[TestClient, Path, Path]:
    """隔离环境:tmp 数据库 + tmp 归档目录(mock ASR,不经音频管线)。"""
    db_path = tmp_path / "agent.db"
    notes_dir = tmp_path / "notes"
    config = AppConfig(
        store=StoreConfig(db_path=str(db_path), notes_dir=str(notes_dir)),
    )
    return TestClient(create_app(config)), db_path, notes_dir


def _seed_draft(
    db_path: Path, draft_id: str, kind: str = "note", record_id: str = "rec-a14"
) -> None:
    """直接落库一条 pending 草稿及其关联 record(device_id 为 NULL 的 PC 来源)。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO records (id, device_id, source, mode, started_at, duration_ms,"
        " status, created_at)"
        " VALUES (?, NULL, 'pc_text', 'field', '2026-07-29T00:00:00+00:00', 0, 'done',"
        " '2026-07-29T00:00:00+00:00')",
        (record_id,),
    )
    conn.execute(
        "INSERT INTO drafts (id, record_id, kind, content_md, status, created_at)"
        " VALUES (?, ?, ?, ?, 'pending', '2026-07-29T00:01:00+00:00')",
        (draft_id, record_id, kind, DRAFT_CONTENT),
    )
    conn.commit()
    conn.close()


def _draft_row(db_path: Path, draft_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    conn.close()
    return row


def _audit_rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    return rows


def test_fr05_confirm_archives_file_and_updates_draft(
    env: tuple[TestClient, Path, Path],
) -> None:
    client, db_path, notes_dir = env
    _seed_draft(db_path, "draft-a14-1")

    resp = client.post("/desk/drafts/draft-a14-1/confirm")
    assert resp.status_code == 200
    assert resp.json() == {"status": "confirmed"}  # 简洁状态,不回传本机路径

    # 文件落盘:文件名只含日期与完整 draft id,正文与草稿逐字一致
    files = list(notes_dir.iterdir())
    assert len(files) == 1
    assert re.fullmatch(r"\d{8}-draft-a14-1\.md", files[0].name), files[0].name
    assert files[0].read_text(encoding="utf-8") == DRAFT_CONTENT

    # drafts 行:confirmed + file_path/confirmed_at 非空
    row = _draft_row(db_path, "draft-a14-1")
    assert row["status"] == "confirmed"
    assert row["file_path"] and row["confirmed_at"]

    # 不再出现在 pending 队列
    pending = client.get("/desk/drafts")
    assert all(d["id"] != "draft-a14-1" for d in pending.json())


def test_fr05_confirm_writes_audit_fields(env: tuple[TestClient, Path, Path]) -> None:
    client, db_path, _ = env
    _seed_draft(db_path, "draft-a14-2")

    assert client.post("/desk/drafts/draft-a14-2/confirm").status_code == 200
    rows = _audit_rows(db_path)
    assert len(rows) == 1
    audit = dict(rows[0])
    assert audit["device_id"] is None  # 不伪造 pc-desk 设备
    assert audit["record_id"] == "rec-a14"
    assert audit["intent"] == "field_note"
    assert audit["tool"] == "notes.archive"
    assert audit["risk_level"] == "L1"
    assert audit["decision"] == "confirmed"
    # 审计不写完整转写文本/草稿正文
    assert audit["params_json"] is None
    assert DRAFT_CONTENT not in str(audit)


def test_fr05_repeat_confirm_409_no_second_audit_no_second_file(
    env: tuple[TestClient, Path, Path],
) -> None:
    client, db_path, notes_dir = env
    _seed_draft(db_path, "draft-a14-3")

    assert client.post("/desk/drafts/draft-a14-3/confirm").status_code == 200
    second = client.post("/desk/drafts/draft-a14-3/confirm")
    assert second.status_code == 409
    assert len(_audit_rows(db_path)) == 1  # 不产生第二条审计
    assert len(list(notes_dir.iterdir())) == 1  # 不产生第二个文件


def test_fr05_experience_draft_409_no_file(env: tuple[TestClient, Path, Path]) -> None:
    client, db_path, notes_dir = env
    _seed_draft(db_path, "draft-a14-4", kind="experience")

    resp = client.post("/desk/drafts/draft-a14-4/confirm")
    assert resp.status_code == 409
    assert not notes_dir.exists() or list(notes_dir.iterdir()) == []
    assert _draft_row(db_path, "draft-a14-4")["status"] == "pending"
    assert _audit_rows(db_path) == []


def test_fr05_unknown_draft_404(env: tuple[TestClient, Path, Path]) -> None:
    client, db_path, notes_dir = env
    resp = client.post("/desk/drafts/no-such-draft/confirm")
    assert resp.status_code == 404
    assert not notes_dir.exists()
    assert _audit_rows(db_path) == []


def test_fr05_draft_repo_confirm_only_pending(tmp_path: Path) -> None:
    """repo 层:UPDATE 带 status='pending' 条件,按受影响行数判定;重复确认返回 False。"""
    from agent_host.store.db import init_db

    conn = init_db(str(tmp_path / "agent.db"))
    drafts = DraftRepo(conn)
    drafts.create(record_id=None, kind="note", content_md="x")
    draft_id = conn.execute("SELECT id FROM drafts").fetchone()[0]

    assert drafts.confirm(draft_id, "data/notes/a.md") is True
    assert drafts.confirm(draft_id, "data/notes/b.md") is False  # 已非 pending
    assert drafts.confirm("missing", "data/notes/c.md") is False  # 不存在
    row = drafts.get(draft_id)
    assert row["status"] == "confirmed"
    assert row["file_path"] == "data/notes/a.md"  # 第二次未覆盖


def test_fr05_local_adapter_same_prefix_ids_no_overwrite(tmp_path: Path) -> None:
    """回归:前 8 位相同的两个完整 draft id,必须生成两个文件且正文分别保留(不静默覆盖)。"""
    from agent_host.adapters.notes import LocalNotesAdapter

    notes = LocalNotesAdapter(str(tmp_path / "notes"))
    p1 = notes.archive("现场记录", "正文甲", "deadbeef-11111111")
    p2 = notes.archive("现场记录", "正文乙", "deadbeef-22222222")
    assert p1 != p2
    assert len(list((tmp_path / "notes").iterdir())) == 2
    assert Path(p1).read_text(encoding="utf-8") == "正文甲"
    assert Path(p2).read_text(encoding="utf-8") == "正文乙"


def test_fr05_mock_adapter_same_title_distinct_ids_no_overwrite() -> None:
    """回归:Mock 连续归档相同 title、不同 draft_id,必须保留两条不同记录。"""
    from agent_host.adapters.notes import MockNotesAdapter

    notes = MockNotesAdapter()
    p1 = notes.archive("现场记录", "正文甲", "deadbeef-11111111")
    p2 = notes.archive("现场记录", "正文乙", "deadbeef-22222222")
    assert p1 != p2
    assert notes._archived[p1] == "正文甲"
    assert notes._archived[p2] == "正文乙"
