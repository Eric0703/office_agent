"""FR-07:一次性定时提醒 —— 路由、解析、直建/澄清/失败、卡片与 PC 工作台可见、到点触发。

产品定义(Owner 决策,2026-07-21):"定时任务" = 一次性、可取消的定时提醒(timer 卡),
非周期 cron。安全约束:不确定必须确认,取消/过期不产生任何写入;
不得扩大对写操作的模糊匹配。全部用例使用临时数据库,不触碰本机运行时数据。
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.adapters.asr import MockASR
from agent_host.adapters.task import MockTaskAdapter
from agent_host.api.app import create_app
from agent_host.config import (
    AppConfig,
    AudioConfig,
    DevConfig,
    ProviderConfig,
    SecurityConfig,
    StoreConfig,
)
from agent_host.router.router import IntentKind, IntentRouter
from agent_host.scheduler.jobs import fire_due
from agent_host.skills.reminder import CANCEL_ID, CONFIRM_ID, ReminderSkill, parse_remind
from agent_host.skills.task_command import TaskCommandSkill
from agent_host.store.db import init_db
from agent_host.store.repos import CardRepo, TaskRepo

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 21, 9, 0, tzinfo=TZ8)  # 固定"今天 09:00",用例确定
TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "l1_synthetic"
FIELD_WAV = TESTDATA / "audio" / "clean" / "FIELD-001.wav"

router = IntentRouter()


@pytest.fixture()
def env(tmp_path: Path) -> tuple[ReminderSkill, CardRepo]:
    conn = init_db(tmp_path / "t.db")
    return ReminderSkill(CardRepo(conn)), CardRepo(conn)


def _intent(text: str) -> str:
    intent = router.route(text)
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "create_reminder"
    return str(intent.entities["remind_query"])


# ---------- 路由与安全护栏 ----------


def test_fr07_route_remind_phrases() -> None:
    """"提醒我 / 定时任务 / 定时提醒"类表达路由到 create_reminder。"""
    assert router.route("明天上午十点提醒我给 WorkBuddy 发周报。").command == "create_reminder"
    assert router.route("创建一个定时任务,明天上午十点发周报。").command == "create_reminder"
    # "创建一个定时任务"不得再落入普通 create_task(Owner 报告的缺陷)


def test_fr07_complete_still_wins_over_remind() -> None:
    """安全:含完成动词的句子仍是 complete_task,不得被提醒创建吞掉。"""
    intent = router.route("把定时任务标记为已完成。")
    assert intent.command == "complete_task"


def test_fr07_cancel_reminder_explicitly_rejected(tmp_path: Path) -> None:
    """取消提醒不在本轮范围:明确拒绝,不产生任何写入。"""
    conn = init_db(tmp_path / "t.db")
    cards = CardRepo(conn)
    skill = TaskCommandSkill(MockTaskAdapter(TaskRepo(conn)), cards)
    intent = router.route("取消明天十点的提醒。")
    assert intent.command == "cancel_reminder"
    result = skill.execute(intent, "rec-c1")
    assert result.status == "failed"
    assert cards.list_by_kind("timer") == []


# ---------- 时间解析 ----------


def test_fr07_parse_explicit_date_and_time() -> None:
    parsed = parse_remind("明天上午十点提醒我给WorkBuddy发周报", NOW)
    assert parsed.remind_at == datetime(2026, 7, 22, 10, 0, tzinfo=TZ8)
    assert parsed.content == "给WorkBuddy发周报"
    assert not parsed.uncertain


def test_fr07_parse_today_afternoon() -> None:
    parsed = parse_remind("今天下午三点提醒我交方案", NOW)
    assert parsed.remind_at == datetime(2026, 7, 21, 15, 0, tzinfo=TZ8)
    assert parsed.content == "交方案"
    assert not parsed.uncertain


def test_fr07_parse_relative_minutes() -> None:
    parsed = parse_remind("十分钟后提醒我起身活动一下", NOW)
    assert parsed.remind_at == NOW + timedelta(minutes=10)
    assert parsed.content == "起身活动"
    assert not parsed.uncertain


def test_fr07_parse_half_hour_and_clock() -> None:
    assert parse_remind("后天晚上八点半提醒我回电话", NOW).remind_at == datetime(
        2026, 7, 23, 20, 30, tzinfo=TZ8
    )
    assert parse_remind("明天10:30提醒我开会", NOW).remind_at == datetime(
        2026, 7, 22, 10, 30, tzinfo=TZ8
    )


def test_fr07_parse_no_time_is_failed() -> None:
    parsed = parse_remind("提醒我交方案", NOW)
    assert parsed.remind_at is None


def test_fr07_parse_missing_date_is_uncertain() -> None:
    parsed = parse_remind("提醒我十点交方案", NOW)
    assert parsed.uncertain
    assert parsed.remind_at == datetime(2026, 7, 21, 10, 0, tzinfo=TZ8)  # 今天 10:00(未来)


def test_fr07_parse_past_time_rolls_to_tomorrow_uncertain() -> None:
    noon = NOW.replace(hour=12)
    parsed = parse_remind("今天上午十点提醒我交方案", noon)
    assert parsed.uncertain
    assert parsed.remind_at == datetime(2026, 7, 22, 10, 0, tzinfo=TZ8)  # 已过,顺延明天


# ---------- 直建 / 澄清 / 失败 ----------


def test_fr07_explicit_creates_timer_card(env: tuple[ReminderSkill, CardRepo]) -> None:
    """明确指令:直接写入 timer 卡,remind_at 为明确 ISO8601。"""
    skill, cards = env
    result = skill.execute(_intent("明天上午十点提醒我给WorkBuddy发周报。"), "rec-r1", now=NOW)
    assert result.status == "success"
    assert "已创建提醒" in result.title
    assert "明天 10:00" in result.title
    due = cards.list_due_active("2099-01-01T00:00:00+08:00")
    assert len(due) == 1
    assert due[0]["kind"] == "timer"
    assert due[0]["title"] == "给WorkBuddy发周报"
    assert due[0]["remind_at"].startswith("2026-07-22T10:00")


def test_fr07_missing_time_failed_no_write(env: tuple[ReminderSkill, CardRepo]) -> None:
    """缺时间:失败提示,不产生任何卡片。"""
    skill, cards = env
    result = skill.execute(_intent("提醒我交方案。"), "rec-r2", now=NOW)
    assert result.status == "failed"
    assert cards.list_by_kind("timer") == []


def test_fr07_missing_content_failed_no_write(env: tuple[ReminderSkill, CardRepo]) -> None:
    """缺内容:失败提示,不产生任何卡片。"""
    skill, cards = env
    result = skill.execute(_intent("明天上午十点提醒我。"), "rec-r3", now=NOW)
    assert result.status == "failed"
    assert cards.list_by_kind("timer") == []


def test_fr07_uncertain_requires_confirm(env: tuple[ReminderSkill, CardRepo]) -> None:
    """不确定(缺日期):先 clarify 展示解析,确认才写入;取消不产生任何写入。"""
    skill, cards = env
    result = skill.execute(_intent("提醒我十点交方案。"), "rec-r4", now=NOW)
    assert result.status == "clarify"
    labels = [c["label"] for c in result.candidates]
    assert any("创建:" in label and "10:00" in label for label in labels)
    assert "取消" in labels
    assert cards.list_by_kind("timer") == []  # 确认前不写入

    cancelled = skill.confirm_pending("rec-r4", CANCEL_ID)
    assert cancelled.status == "success"
    assert "未创建" in cancelled.title
    assert cards.list_by_kind("timer") == []

    again = skill.execute(_intent("提醒我十点交方案。"), "rec-r5", now=NOW)
    assert again.status == "clarify"
    confirmed = skill.confirm_pending("rec-r5", CONFIRM_ID)
    assert confirmed.status == "success"
    assert "已创建提醒" in confirmed.title
    assert len(cards.list_by_kind("timer")) == 1


def test_fr07_expired_pending_writes_nothing(env: tuple[ReminderSkill, CardRepo]) -> None:
    """未知/过期确认:失败,不写入。"""
    skill, cards = env
    result = skill.confirm_pending("no-such-record", CONFIRM_ID)
    assert result.status == "failed"
    assert cards.list_by_kind("timer") == []


# ---------- 复合句:提醒 + 多任务 ----------


def test_fr07_compound_remind_with_tasks(tmp_path: Path) -> None:
    """"提醒我明天九点半开会,提前准备会议论文,然后整理 X,这是两个任务。"
    不得合并成单条提醒;clarify 呈现 1 提醒 + 2 任务;确认后全部写入,取消无写入。"""
    conn = init_db(tmp_path / "t.db")
    cards = CardRepo(conn)
    tasks = TaskRepo(conn)
    skill = ReminderSkill(cards, tasks)
    intent = router.route(
        "提醒我明天九点半开会,提前准备会议论文,然后整理WorkBuddy和Codex的使用说明,这是两个任务。"
    )
    assert intent.command == "create_reminder"
    result = skill.execute(str(intent.entities["remind_query"]), "rec-cp1", now=NOW)
    assert result.status == "clarify"
    assert "1 个提醒和 2 个任务" in result.title
    body = result.body or ""
    assert "09:30" in body and "开会" in body
    assert "准备会议论文" in body
    assert "整理WorkBuddy和Codex的使用说明" in body
    assert cards.list_by_kind("timer") == []
    assert tasks.list_all() == []  # 确认前不写入

    confirmed = skill.confirm_pending("rec-cp1", CONFIRM_ID)
    assert confirmed.status == "success"
    assert "2 个任务" in confirmed.title
    due = cards.list_due_active("2099-01-01T00:00:00+08:00")
    assert len(due) == 1
    assert due[0]["title"] == "开会"
    assert due[0]["remind_at"].startswith("2026-07-22T09:30")
    stored = [t["title"] for t in tasks.list_all()]
    assert "准备会议论文" in stored
    assert "整理WorkBuddy和Codex的使用说明" in stored


def test_fr07_compound_cancel_writes_nothing(tmp_path: Path) -> None:
    """复合预览取消:不产生提醒卡也不产生任务。"""
    conn = init_db(tmp_path / "t.db")
    cards = CardRepo(conn)
    skill = ReminderSkill(cards, TaskRepo(conn))
    intent = router.route("提醒我明天九点半开会,提前准备会议论文,然后整理文档,这是两个任务。")
    result = skill.execute(str(intent.entities["remind_query"]), "rec-cp2", now=NOW)
    assert result.status == "clarify"
    cancelled = skill.confirm_pending("rec-cp2", CANCEL_ID)
    assert cancelled.status == "success"
    assert "未创建" in cancelled.title
    assert cards.list_by_kind("timer") == []


# ---------- 到点触发(最小调度) ----------


def test_fr07_fire_due_broadcasts_once(tmp_path: Path) -> None:
    """到期 timer 卡触发一次 reminder.push;未到期不触发;内存去重不重复触发。"""
    conn = init_db(tmp_path / "t.db")
    cards = CardRepo(conn)
    cards.upsert("c-due", "timer", "到期提醒", remind_at="2026-07-21T08:00:00+08:00")
    cards.upsert("c-future", "timer", "未来提醒", remind_at="2099-01-01T00:00:00+08:00")
    cards.upsert("c-task", "task", "任务卡(非提醒)", remind_at="2026-07-21T08:00:00+08:00")
    sent: list[tuple[str, dict]] = []

    async def fake_broadcast(msg_type: str, payload: dict) -> None:
        sent.append((msg_type, payload))

    fired: set[str] = set()
    count = asyncio.run(fire_due(cards, fake_broadcast, fired, now=NOW))
    assert count == 1
    assert sent == [("reminder.push", {"card": {
        "card_id": "c-due", "kind": "timer", "title": "到期提醒",
        "body": None, "remind_at": "2026-07-21T08:00:00+08:00", "ref_task_id": None,
    }})]
    assert asyncio.run(fire_due(cards, fake_broadcast, fired, now=NOW)) == 0  # 不重复触发


# ---------- 端到端(注入 MockASR,临时数据库) ----------


def _make_client(tmp_path: Path, mock_text: str) -> TestClient:
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
    app = create_app(config, asr=MockASR(text=mock_text, confidence=0.99))
    conn = sqlite3.connect(str(tmp_path / "agent.db"))
    conn.execute(
        "INSERT INTO devices (id, name, token_hash, paired_at) VALUES (?, ?, ?, ?)",
        ("dev-1", "测试设备", "x", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return TestClient(app)


def _upload(client: TestClient, record_id: str) -> None:
    resp = client.post(
        f"/audio/{record_id}",
        headers={"X-Device-Id": "dev-1", "X-Audio-Format": "wav", "X-Duration-Ms": "3000"},
        content=FIELD_WAV.read_bytes(),
    )
    assert resp.status_code == 200
    # BackgroundTasks:响应返回时管线已执行完(与 uvicorn 行为一致)


def test_fr07_reminder_visible_in_desk(tmp_path: Path) -> None:
    """全链路:上传 → 直建 timer 卡 → PC 工作台"待办任务 / 提醒"可见。"""
    client = _make_client(tmp_path, "明天上午十点提醒我给WorkBuddy发周报。")
    _upload(client, "rec-e2e-remind")
    tasks = client.get("/desk/tasks").json()
    mine = [t for t in tasks if t["kind"] == "timer" and "WorkBuddy" in t["title"]]
    assert len(mine) == 1
    assert mine[0]["status"] == "生效中"
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date().isoformat()
    assert mine[0]["time"].startswith(f"{tomorrow}T10:00")  # 明确 ISO8601,非"明天"字符串


def test_fr07_create_task_visible_in_desk(tmp_path: Path) -> None:
    """普通任务创建成功后,PC 工作台可见(不只是工牌短暂提示)。"""
    client = _make_client(tmp_path, "新建任务,明天之前回复客户邮件。")
    _upload(client, "rec-e2e-task")
    tasks = client.get("/desk/tasks").json()
    mine = [t for t in tasks if t["kind"] == "task" and "回复客户邮件" in t["title"]]
    assert len(mine) == 1
    assert mine[0]["status"] == "未完成"
    # 相对期限落为稳定日期(FR-08),不永久保存裸"明天"
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date().isoformat()
    assert mine[0]["time"] == tomorrow
