"""FR-04 补充:来源中立的 PC 文字入口(Owner 2026-07-22)。

process_text 不创建设备、不需要预建录音 Record、不经音频/HTTP/WebSocket;
records 真实登记来源(source='pc_text',device_id NULL);audit 的 device_id 落 NULL;
投递目标不写死在 ProcessOutcome(只有 record_id + 消息序列)。
临时数据库,不触碰本机运行时数据。
"""

import sqlite3
from pathlib import Path

import pytest

from agent_host.adapters.task import MockTaskAdapter
from agent_host.audit.logger import AuditLogger
from agent_host.core.processing import ProcessingDeps, process_text
from agent_host.router.router import IntentRouter
from agent_host.skills.experience import ExperienceSkill
from agent_host.skills.field_note import FieldNoteSkill
from agent_host.skills.reminder import ReminderSkill
from agent_host.skills.task_command import TaskCommandSkill
from agent_host.store.db import init_db
from agent_host.store.repos import AuditRepo, CardRepo, DraftRepo, RecordRepo, TaskRepo


@pytest.fixture()
def env(tmp_path: Path) -> tuple[ProcessingDeps, sqlite3.Connection, list[dict]]:
    """真实 repos/router/skills 装配的 ProcessingDeps;注意:不创建任何设备。"""
    conn = init_db(tmp_path / "t.db")
    cached: list[dict] = []
    deps = ProcessingDeps(
        records=RecordRepo(conn),
        router=IntentRouter(),
        field_notes=FieldNoteSkill(DraftRepo(conn)),
        experience=ExperienceSkill(DraftRepo(conn)),
        reminders=ReminderSkill(CardRepo(conn), TaskRepo(conn)),
        task_commands=TaskCommandSkill(MockTaskAdapter(TaskRepo(conn)), CardRepo(conn)),
        audit=AuditLogger(AuditRepo(conn)),
        low_confidence_threshold=0.5,
        cache_result=cached.append,
    )
    return deps, conn, cached


def test_fr04_pc_text_without_device_or_record(
    env: tuple[ProcessingDeps, sqlite3.Connection, list],
) -> None:
    """PC 文字直调核心:无 Device、无预建录音 Record、不经音频/HTTP/WS。"""
    deps, conn, cached = env
    outcome = process_text(
        deps,
        text="明天上午十点提醒我给WorkBuddy发周报。",
        confidence=1.0,
        source="pc_text",
    )
    # 业务结果与设备音频路径一致:success intent.result;outcome 不含投递目标
    assert outcome.record_id.startswith("rec-")
    assert [m.msg_type for m in outcome.messages] == ["intent.result"]
    payload = outcome.messages[0].payload
    assert payload["record_id"] == outcome.record_id
    assert payload["status"] == "success"
    assert "已创建提醒" in payload["title"]
    assert not hasattr(outcome, "device_id")
    # 持久化记录表达来源类型与可选设备信息
    row = conn.execute(
        "SELECT source, device_id, status, transcript FROM records WHERE id = ?",
        (outcome.record_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "pc_text"
    assert row[1] is None
    assert row[2] == "done"
    assert "WorkBuddy" in row[3]
    # 未伪造设备:devices 表零行
    assert conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0
    # 审计落 device_id NULL
    audits = conn.execute(
        "SELECT device_id, decision, intent FROM audit_log WHERE record_id = ?",
        (outcome.record_id,),
    ).fetchall()
    assert [tuple(r) for r in audits] == [(None, "executed", "create_reminder")]
    assert cached == [payload]
    # 定时提醒真实生效:timer 卡已建
    assert conn.execute("SELECT COUNT(*) FROM cards WHERE kind = 'timer'").fetchone()[0] == 1


def test_fr04_pc_text_field_note_draft(
    env: tuple[ProcessingDeps, sqlite3.Connection, list],
) -> None:
    """PC 文字走现场记录:草稿生成,records source='pc_text',audit executed/L0。"""
    deps, conn, cached = env
    outcome = process_text(
        deps,
        text="现场看到三号流水线温度偏高,已通知班组复查,后续继续观察。",
        confidence=1.0,
        source="pc_text",
        mode="field",
    )
    payload = outcome.messages[0].payload
    assert payload["status"] == "success"
    assert payload["title"] == "笔记草稿已生成"
    row = conn.execute(
        "SELECT source, device_id, status FROM records WHERE id = ?", (outcome.record_id,)
    ).fetchone()
    assert tuple(row) == ("pc_text", None, "done")
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
    audits = conn.execute(
        "SELECT device_id, decision, risk_level FROM audit_log WHERE record_id = ?",
        (outcome.record_id,),
    ).fetchall()
    assert [tuple(r) for r in audits] == [(None, "executed", "L0")]


def test_fr04_legacy_records_upgrade(tmp_path: Path) -> None:
    """旧结构 records(无 source、device_id NOT NULL)幂等升级:数据保留,可写 pc_text。"""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL,
                              paired_at TEXT NOT NULL, revoked_at TEXT, last_seen_at TEXT);
        CREATE TABLE records (
          id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
          mode TEXT NOT NULL CHECK (mode IN ('auto','field','experience')),
          started_at TEXT NOT NULL, duration_ms INTEGER NOT NULL, audio_tmp_path TEXT,
          status TEXT NOT NULL CHECK (status IN
            ('uploaded','transcribed','routed','done','failed')),
          transcript TEXT, confidence REAL, intent TEXT, created_at TEXT NOT NULL);
        INSERT INTO devices VALUES ('dev-old','旧设备','x','2026-01-01',NULL,NULL);
        INSERT INTO records (id, device_id, mode, started_at, duration_ms, status, created_at)
          VALUES ('rec-old','dev-old','auto','2026-01-01',3000,'done','2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    upgraded = init_db(db_path)
    row = upgraded.execute(
        "SELECT id, device_id, source, status FROM records WHERE id = 'rec-old'"
    ).fetchone()
    assert tuple(row) == ("rec-old", "dev-old", "device_audio", "done")  # 旧数据保留,source 默认
    # 可写 pc_text(device_id NULL)
    RecordRepo(upgraded).create(
        record_id="rec-pc",
        device_id=None,
        mode="auto",
        started_at="2026-01-02T00:00:00+00:00",
        duration_ms=0,
        source="pc_text",
    )
    assert upgraded.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    # 引用完整性:drafts 外键指向 records(非 records_old);foreign_key_check 为空
    fk_tables = [r[2] for r in upgraded.execute("PRAGMA foreign_key_list(drafts)").fetchall()]
    assert fk_tables == ["records"]
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    # 可插入引用旧记录的 draft
    upgraded.execute(
        "INSERT INTO drafts (id, record_id, kind, content_md, status, created_at)"
        " VALUES ('d-old', 'rec-old', 'note', '正文', 'pending', '2026-01-02')"
    )
    upgraded.commit()
    assert upgraded.execute(
        "SELECT record_id FROM drafts WHERE id = 'd-old'"
    ).fetchone()[0] == "rec-old"
    upgraded.close()
    # 幂等:再次 init 零变化
    again = init_db(db_path)
    assert again.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    again.close()
