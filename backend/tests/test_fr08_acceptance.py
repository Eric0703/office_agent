"""FR-08 验收补测(A1-5 差距核对):既有实现已覆盖的,不重复;本文件只补缺失断言。

补测项:合法候选只完成其一、缺标题失败、非法预览选择、remind:confirm 只创建一次、
查询今日任务只读(空/非空)、duplicate 重复上传不产生两条任务。
全部临时数据库,不触碰本机运行时数据;不修改任何生产代码。
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.adapters.asr import MockASR
from agent_host.adapters.task import MockTaskAdapter
from agent_host.api.app import create_app
from agent_host.audit.logger import AuditLogger
from agent_host.config import (
    AppConfig,
    AudioConfig,
    DevConfig,
    ProviderConfig,
    SecurityConfig,
    StoreConfig,
)
from agent_host.core.processing import ProcessingDeps, on_clarify, process_text
from agent_host.router.router import Intent, IntentKind, IntentRouter
from agent_host.skills.experience import ExperienceSkill
from agent_host.skills.field_note import FieldNoteSkill
from agent_host.skills.reminder import CONFIRM_ID, ReminderSkill
from agent_host.skills.task_command import CONFIRM_ALL_ID, TaskCommandSkill
from agent_host.store.db import init_db
from agent_host.store.repos import AuditRepo, CardRepo, DeviceRepo, DraftRepo, RecordRepo, TaskRepo

DEVICE_ID = "dev-a15"
TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 21, 9, 0, tzinfo=TZ8)  # 固定"今天 09:00",用例确定


@pytest.fixture()
def env(tmp_path: Path) -> tuple[ProcessingDeps, sqlite3.Connection]:
    """真实 repos/router/skills 装配的 ProcessingDeps(cache_result 内存桩)。"""
    conn = init_db(tmp_path / "t.db")
    DeviceRepo(conn).create(
        device_id=DEVICE_ID,
        name="测试设备",
        token_hash="x",
        paired_at="2026-01-01T00:00:00+00:00",
    )
    deps = ProcessingDeps(
        records=RecordRepo(conn),
        router=IntentRouter(),
        field_notes=FieldNoteSkill(DraftRepo(conn)),
        experience=ExperienceSkill(DraftRepo(conn)),
        reminders=ReminderSkill(CardRepo(conn), TaskRepo(conn)),
        task_commands=TaskCommandSkill(MockTaskAdapter(TaskRepo(conn)), CardRepo(conn)),
        audit=AuditLogger(AuditRepo(conn)),
        low_confidence_threshold=0.5,
        cache_result=lambda payload: None,
    )
    return deps, conn


def _seed_record(deps: ProcessingDeps, record_id: str) -> None:
    deps.records.create(
        record_id=record_id,
        device_id=DEVICE_ID,
        mode="auto",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=3000,
    )


def test_fr08_clarify_select_completes_only_chosen(
    env: tuple[ProcessingDeps, sqlite3.Connection],
) -> None:
    """合法候选选择:只完成选定任务,另一相似任务保持 open;audit executed 一条。"""
    deps, conn = env
    tasks = TaskRepo(conn)
    tasks.insert(title="回复客户邮件", task_id="t-mail")
    tasks.insert(title="回复老板消息", task_id="t-boss")
    _seed_record(deps, "rec-a15-pick")
    outcome = process_text(
        deps, record_id="rec-a15-pick", text="把回复标记为已完成。",
        confidence=0.99, device_id=DEVICE_ID,
    )
    assert outcome.messages[0].payload["status"] == "clarify"

    pick = on_clarify(
        deps, device_id=DEVICE_ID, record_id="rec-a15-pick", candidate_id="t-mail"
    )
    assert pick is not None
    assert pick.messages[0].payload["status"] == "success"
    assert tasks.get("t-mail")["status"] == "done"
    assert tasks.get("t-boss")["status"] == "open"  # 未选中候选不受影响
    status = conn.execute(
        "SELECT status FROM records WHERE id = 'rec-a15-pick'"
    ).fetchone()[0]
    assert status == "done"
    audits = conn.execute(
        "SELECT decision, intent FROM audit_log WHERE record_id = 'rec-a15-pick'"
    ).fetchall()
    assert [tuple(r) for r in audits] == [("executed", "complete_task")]


def test_fr08_create_task_missing_title_fails(tmp_path: Path) -> None:
    """缺标题:失败提示,不猜测创建。"""
    conn = init_db(tmp_path / "t.db")
    tasks = TaskRepo(conn)
    skill = TaskCommandSkill(MockTaskAdapter(tasks), CardRepo(conn))
    intent = Intent(kind=IntentKind.TASK_COMMAND, command="create_task", entities={})
    result = skill.execute(intent, "rec-a15-notitle")
    assert result.status == "failed"
    assert "没听清" in result.title
    assert tasks.list_all() == []


def test_fr08_confirm_create_unknown_candidate_rejected(tmp_path: Path) -> None:
    """多任务预览:非法 candidate_id(非 confirm_all/cancel)失败,不创建任何任务。"""
    conn = init_db(tmp_path / "t.db")
    tasks = TaskRepo(conn)
    skill = TaskCommandSkill(MockTaskAdapter(tasks), CardRepo(conn))
    intent = IntentRouter().route("写周报,另外交报销单。")
    assert skill.execute(intent, "rec-a15-bad").status == "clarify"
    result = skill.confirm_create("rec-a15-bad", "task:bogus")
    assert result.status == "failed"
    assert tasks.list_all() == []
    # 预览已被弹掉:再用合法 confirm_all 也按过期处理,不得创建
    assert skill.confirm_create("rec-a15-bad", CONFIRM_ALL_ID).status == "failed"
    assert tasks.list_all() == []


def test_fr08_remind_confirm_twice_creates_once(tmp_path: Path) -> None:
    """remind:confirm 只创建一次:确认后挂起即清除,二次确认按过期失败,卡仍 1 张。"""
    conn = init_db(tmp_path / "t.db")
    cards = CardRepo(conn)
    skill = ReminderSkill(cards, TaskRepo(conn))
    result = skill.execute("提醒我十点交方案", "rec-a15-rem", now=NOW)
    assert result.status == "clarify"  # 缺日期 → 候选确认

    assert skill.confirm_pending("rec-a15-rem", CONFIRM_ID).status == "success"
    assert len(cards.list_by_kind("timer")) == 1
    second = skill.confirm_pending("rec-a15-rem", CONFIRM_ID)
    assert second.status == "failed"
    assert "过期" in second.title
    assert len(cards.list_by_kind("timer")) == 1  # 不重复创建


def test_fr08_list_today_readonly_empty_and_nonempty(tmp_path: Path) -> None:
    """查询今日任务:只读(不产生任务/卡片副作用);空与非空列表结果均正确。"""
    clock_now = datetime(2026, 7, 29, 9, 0, tzinfo=TZ8)
    conn = init_db(tmp_path / "t.db")
    tasks = TaskRepo(conn)
    cards = CardRepo(conn)
    skill = TaskCommandSkill(MockTaskAdapter(tasks, now_fn=lambda: clock_now), cards)
    router = IntentRouter()

    empty = skill.execute(router.route("查一下还有哪些没完成的任务。"), "rec-a15-l0")
    assert empty.status == "success"
    assert "无未完成任务" in (empty.body or "")
    assert tasks.list_all() == []  # 查询无副作用
    assert cards.list_active() == []

    tasks.insert(title="回复客户邮件", task_id="t-mail", due_at="2026-07-29")
    listed = skill.execute(router.route("查一下还有哪些没完成的任务。"), "rec-a15-l1")
    assert listed.status == "success"
    assert "回复客户邮件" in (listed.body or "")
    assert len(tasks.list_all()) == 1  # 仍只有既有任务
    assert cards.list_active() == []


def test_fr08_list_today_filters_by_due_date(tmp_path: Path) -> None:
    """今日任务 = due_at 日期等于本机今日且 open;明天/无截止/已完成/旧裸期限均不返回。"""
    clock_now = datetime(2026, 7, 29, 9, 0, tzinfo=TZ8)
    conn = init_db(tmp_path / "t.db")
    repo = TaskRepo(conn)
    repo.insert(title="今天截止", due_at="2026-07-29")
    repo.insert(title="明天截止", due_at="2026-07-30")
    repo.insert(title="无截止")
    done_id = repo.insert(title="今天但已完成", due_at="2026-07-29")
    repo.mark_done(done_id, "pc")
    repo.insert(title="旧裸期限", due_at="明天")  # 旧不可解析值:安全忽略,不报错

    adapter = MockTaskAdapter(repo, now_fn=lambda: clock_now)
    assert [t.title for t in adapter.list_today()] == ["今天截止"]


def test_fr08_relative_due_saved_as_stable_date(tmp_path: Path) -> None:
    """"明天之前"落成稳定日期;时钟前进一天后按日期命中今日查询(不永远是"明天")。"""
    clock = {"now": datetime(2026, 7, 29, 9, 0, tzinfo=TZ8)}
    conn = init_db(tmp_path / "t.db")
    repo = TaskRepo(conn)
    adapter = MockTaskAdapter(repo, now_fn=lambda: clock["now"])
    skill = TaskCommandSkill(adapter, CardRepo(conn), now_fn=lambda: clock["now"])

    intent = IntentRouter().route("新建一个任务,明天之前回复客户邮件。")
    assert skill.execute(intent, "rec-due").status == "success"
    assert repo.list_all()[0]["due_at"] == "2026-07-30"  # 稳定 YYYY-MM-DD
    assert adapter.list_today() == []  # 今天查不到明天的任务

    clock["now"] = datetime(2026, 7, 30, 9, 0, tzinfo=TZ8)  # 到第二天
    assert [t.title for t in adapter.list_today()] == ["回复客户邮件"]


def test_fr08_confirm_create_atomic_rollback_and_retry(tmp_path: Path) -> None:
    """故障注入:第二条任务 INSERT 失败 → 整体回滚(tasks=0)、预览保留;修复后重试成功。"""
    conn = init_db(tmp_path / "t.db")
    tasks = TaskRepo(conn)
    skill = TaskCommandSkill(MockTaskAdapter(tasks), CardRepo(conn))
    intent = IntentRouter().route("写周报,另外交报销单。")
    assert skill.execute(intent, "rec-atomic").status == "clarify"

    conn.execute(
        "CREATE TRIGGER fail_second BEFORE INSERT ON tasks WHEN NEW.title = '交报销单'"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.Error):
        skill.confirm_create("rec-atomic", CONFIRM_ALL_ID)
    assert tasks.list_all() == []  # 第一条也随之回滚,不留部分数据

    conn.execute("DROP TRIGGER fail_second")
    conn.commit()
    result = skill.confirm_create("rec-atomic", CONFIRM_ALL_ID)  # 预览保留,可重试
    assert result.status == "success"
    assert len(tasks.list_all()) == 2
    # 成功后再次确认按过期处理,不重复创建
    assert skill.confirm_create("rec-atomic", CONFIRM_ALL_ID).status == "failed"
    assert len(tasks.list_all()) == 2


def test_fr08_compound_confirm_atomic_rollback_and_retry(tmp_path: Path) -> None:
    """故障注入:复合确认第二个任务失败 → timer 卡与任务整体回滚;修复后重试成功。"""
    conn = init_db(tmp_path / "t.db")
    cards = CardRepo(conn)
    tasks = TaskRepo(conn)
    skill = ReminderSkill(cards, tasks)
    result = skill.execute(
        "提醒我明天九点半开会,提前准备会议论文,然后整理文档", "rec-compound", now=NOW
    )
    assert result.status == "clarify"  # 1 提醒 + 2 任务复合预览

    conn.execute(
        "CREATE TRIGGER fail_task2 BEFORE INSERT ON tasks WHEN NEW.title = '整理文档'"
        " BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.Error):
        skill.confirm_pending("rec-compound", CONFIRM_ID)
    assert cards.list_by_kind("timer") == []  # timer 卡也回滚
    assert tasks.list_all() == []

    conn.execute("DROP TRIGGER fail_task2")
    conn.commit()
    ok = skill.confirm_pending("rec-compound", CONFIRM_ID)  # pending 保留,可重试
    assert ok.status == "success"
    assert len(cards.list_by_kind("timer")) == 1
    assert len(tasks.list_all()) == 2
    # 第二次成功确认不得重复创建(pending 已清除,按过期处理)
    assert skill.confirm_pending("rec-compound", CONFIRM_ID).status == "failed"
    assert len(cards.list_by_kind("timer")) == 1
    assert len(tasks.list_all()) == 2


def test_fr08_duplicate_upload_no_double_create(tmp_path: Path) -> None:
    """端到端幂等:同一 record_id 重复上传,第二次 duplicate,不产生两条任务/两条审计。"""
    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        security=SecurityConfig(
            whitelist_commands=[
                "complete_task", "list_today_tasks", "create_task", "create_reminder",
            ]
        ),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    client = TestClient(
        create_app(config, asr=MockASR(text="新建任务,明天之前回复客户邮件。", confidence=0.99))
    )
    conn = sqlite3.connect(str(tmp_path / "agent.db"))
    conn.execute(
        "INSERT INTO devices (id, name, token_hash, paired_at) VALUES (?, ?, ?, ?)",
        ("dev-a15-e2e", "测试设备", "x", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    headers = {"X-Device-Id": "dev-a15-e2e", "X-Audio-Format": "wav", "X-Duration-Ms": "3000"}
    first = client.post("/audio/rec-a15-dup", headers=headers, content=b"audio")
    assert first.status_code == 200
    second = client.post("/audio/rec-a15-dup", headers=headers, content=b"audio")
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    conn = sqlite3.connect(str(tmp_path / "agent.db"))
    task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    audits = conn.execute(
        "SELECT decision, intent FROM audit_log WHERE record_id = 'rec-a15-dup'"
    ).fetchall()
    conn.close()
    assert task_count == 1  # 重复上传不产生第二条任务
    assert [tuple(r) for r in audits] == [("executed", "create_task")]  # 审计仅一条
