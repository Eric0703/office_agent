"""FR-04 补充:文本处理入口复用 —— core.process_text 不经 HTTP/WS/ASR 直接驱动。

覆盖:提醒直建(成功)、完成任务(intent.result 在前、reminder.dismiss 在后的顺序契约)、
歧义任务(clarify 中间态,非终态不审计)、低置信度闸门、unknown;
落库/缓存/审计在返回 outcome 之前完成。临时数据库,不触碰本机运行时数据。
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
from agent_host.store.repos import AuditRepo, CardRepo, DeviceRepo, DraftRepo, RecordRepo, TaskRepo

DEVICE_ID = "dev-t"


@pytest.fixture()
def env(tmp_path: Path) -> tuple[ProcessingDeps, sqlite3.Connection, list[dict]]:
    """真实 repos/router/skills 装配的 ProcessingDeps(仅 cache_result 用内存桩)。"""
    conn = init_db(tmp_path / "t.db")
    DeviceRepo(conn).create(
        device_id=DEVICE_ID,
        name="测试设备",
        token_hash="x",
        paired_at="2026-01-01T00:00:00+00:00",
    )  # records.device_id 外键依赖 devices
    cards = CardRepo(conn)
    tasks = TaskRepo(conn)
    drafts = DraftRepo(conn)
    cached: list[dict] = []
    deps = ProcessingDeps(
        records=RecordRepo(conn),
        router=IntentRouter(),
        field_notes=FieldNoteSkill(drafts),
        experience=ExperienceSkill(drafts),
        reminders=ReminderSkill(cards, tasks),
        task_commands=TaskCommandSkill(MockTaskAdapter(tasks), cards),
        audit=AuditLogger(AuditRepo(conn)),
        low_confidence_threshold=0.5,
        cache_result=cached.append,
    )
    return deps, conn, cached


def _seed_record(deps: ProcessingDeps, record_id: str) -> None:
    deps.records.create(
        record_id=record_id,
        device_id=DEVICE_ID,
        mode="auto",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=3000,
    )


def _status(conn: sqlite3.Connection, record_id: str) -> str:
    row = conn.execute("SELECT status FROM records WHERE id = ?", (record_id,)).fetchone()
    assert row is not None
    return str(row[0])


def _audits(conn: sqlite3.Connection, record_id: str) -> list[tuple]:
    rows = conn.execute(
        "SELECT decision, intent, risk_level FROM audit_log WHERE record_id = ?", (record_id,)
    ).fetchall()
    return [tuple(r) for r in rows]


def test_fr04_reminder_text_success(env: tuple[ProcessingDeps, sqlite3.Connection, list]) -> None:
    """提醒文本:outcome 含 success intent.result,records=done,audit=executed。"""
    deps, conn, cached = env
    _seed_record(deps, "rec-pt-remind")
    outcome = process_text(
        deps,
        record_id="rec-pt-remind",
        text="明天上午十点提醒我给WorkBuddy发周报。",
        confidence=0.99,
        mode="auto",
        device_id=DEVICE_ID,
    )
    assert outcome.record_id == "rec-pt-remind"
    assert [m.msg_type for m in outcome.messages] == ["intent.result"]
    payload = outcome.messages[0].payload
    assert payload["record_id"] == "rec-pt-remind"
    assert payload["status"] == "success"
    assert "已创建提醒" in payload["title"]
    assert _status(conn, "rec-pt-remind") == "done"
    assert _audits(conn, "rec-pt-remind") == [("executed", "create_reminder", "L1")]
    assert cached == [payload]  # 缓存时机:返回 outcome 之前已完成


def test_fr04_complete_task_message_order(
    env: tuple[ProcessingDeps, sqlite3.Connection, list],
) -> None:
    """说完即消:intent.result 在前,逐卡 reminder.dismiss 在后(顺序与现行推送一致)。"""
    deps, conn, cached = env
    _seed_record(deps, "rec-pt-done")
    task_id = TaskRepo(conn).insert(title="回复客户邮件")
    CardRepo(conn).upsert("card-1", "task", "回复客户邮件", ref_task_id=task_id)
    outcome = process_text(
        deps,
        record_id="rec-pt-done",
        text="把回复客户邮件标记为已完成。",
        confidence=0.99,
        mode="auto",
        device_id=DEVICE_ID,
    )
    assert [m.msg_type for m in outcome.messages] == ["intent.result", "reminder.dismiss"]
    first, second = outcome.messages
    assert first.payload["record_id"] == "rec-pt-done"
    assert first.payload["status"] == "success"
    assert "已完成" in first.payload["title"]
    assert second.payload == {"card_id": "card-1", "reason": "completed"}
    assert _status(conn, "rec-pt-done") == "done"
    assert _audits(conn, "rec-pt-done") == [("executed", "complete_task", "L1")]
    assert cached == [first.payload]


def test_fr04_ambiguous_task_clarify(env: tuple[ProcessingDeps, sqlite3.Connection, list]) -> None:
    """歧义任务文本:clarify 候选 outcome,records 非终态,终态前不审计。"""
    deps, conn, cached = env
    _seed_record(deps, "rec-pt-ambig")
    tasks = TaskRepo(conn)
    tasks.insert(title="回复客户邮件")
    tasks.insert(title="回复老板消息")
    outcome = process_text(
        deps,
        record_id="rec-pt-ambig",
        text="把回复标记为已完成。",
        confidence=0.99,
        mode="auto",
        device_id=DEVICE_ID,
    )
    assert [m.msg_type for m in outcome.messages] == ["intent.result"]
    payload = outcome.messages[0].payload
    assert payload["status"] == "clarify"
    assert len(payload["candidates"]) == 2
    assert _status(conn, "rec-pt-ambig") not in ("done", "failed")  # 中间态,等 clarify.select
    assert _audits(conn, "rec-pt-ambig") == []
    assert cached == [payload]


def test_fr04_low_confidence(env: tuple[ProcessingDeps, sqlite3.Connection, list]) -> None:
    """低 confidence:low_confidence 载荷,records=failed。"""
    deps, conn, cached = env
    _seed_record(deps, "rec-pt-low")
    outcome = process_text(
        deps,
        record_id="rec-pt-low",
        text="任意文本。",
        confidence=0.3,
        mode="auto",
        device_id=DEVICE_ID,
    )
    assert [m.msg_type for m in outcome.messages] == ["intent.result"]
    payload = outcome.messages[0].payload
    assert payload["record_id"] == "rec-pt-low"
    assert payload["status"] == "low_confidence"
    assert payload["title"] == "没有听清,请重新录音"
    assert payload["error_code"] == "ASR_LOW_CONFIDENCE"
    assert _status(conn, "rec-pt-low") == "failed"
    assert cached == [payload]


def test_fr04_unknown_intent(env: tuple[ProcessingDeps, sqlite3.Connection, list]) -> None:
    """unknown:failed 载荷(INTENT_UNKNOWN),records=failed,audit=failed。"""
    deps, conn, cached = env
    _seed_record(deps, "rec-pt-unknown")
    outcome = process_text(
        deps,
        record_id="rec-pt-unknown",
        text="……",  # 归一化后为空:路由 unknown,不猜测执行
        confidence=0.99,
        mode="auto",
        device_id=DEVICE_ID,
    )
    assert [m.msg_type for m in outcome.messages] == ["intent.result"]
    payload = outcome.messages[0].payload
    assert payload["record_id"] == "rec-pt-unknown"
    assert payload["status"] == "failed"
    assert payload["title"] == "没有理解,请换种说法"
    assert payload["error_code"] == "INTENT_UNKNOWN"
    assert _status(conn, "rec-pt-unknown") == "failed"
    assert _audits(conn, "rec-pt-unknown") == [("failed", "unknown", None)]
    assert cached == [payload]
