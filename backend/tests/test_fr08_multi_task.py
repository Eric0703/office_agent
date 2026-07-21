"""FR-08 补充(2026-07-21):多任务拆分、可编辑预览确认、desk 完成/取消接口。

安全约束:多任务/口语猜测一律经预览确认,确认前不写入;"和"、"、"不切分;
edited_labels 为空不得创建;全部用例使用临时数据库。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.adapters.task import MockTaskAdapter
from agent_host.api.app import create_app
from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig
from agent_host.router.router import IntentKind, IntentRouter
from agent_host.skills.task_command import (
    CONFIRM_ALL_ID,
    TaskCommandSkill,
    split_tasks,
)
from agent_host.store.db import init_db
from agent_host.store.repos import CardRepo, TaskRepo

TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "l1_synthetic"
router = IntentRouter()

Env = tuple[TaskCommandSkill, CardRepo, TaskRepo]


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    conn = init_db(tmp_path / "t.db")
    tasks = TaskRepo(conn)
    cards = CardRepo(conn)
    return TaskCommandSkill(MockTaskAdapter(tasks), cards), cards, tasks


# ---------- 拆分规则 ----------


def test_fr08_split_owner_example() -> None:
    """Owner 示例:"下午要准备会议论文,另外把 WorkBuddy 和 Codex 的使用说明整理一下。"""
    titles = split_tasks("下午要准备会议论文另外把WorkBuddy和Codex的使用说明整理一下")
    assert titles == ["准备会议论文", "整理WorkBuddy和Codex的使用说明"]


def test_fr08_split_keeps_conjunction_objects() -> None:
    """"和"、"、"连接的是并列宾语,不得拆成两条任务。"""
    titles = split_tasks("买牛奶、鸡蛋和面包")
    assert titles == ["买牛奶、鸡蛋和面包"]


def test_fr08_split_punctuation_and_more_markers() -> None:
    titles = split_tasks("写周报,交报销单;还有联系客户")
    assert titles == ["写周报", "交报销单", "联系客户"]


def test_fr08_split_caps_at_five() -> None:
    titles = split_tasks("写一,写二,写三,写四,写五,写六,写七")
    assert len(titles) == 5
    assert "写七" in titles[-1]


# ---------- 路由:口语并列待办 ----------


def test_fr08_route_bare_spoken_multi_tasks() -> None:
    """无"新建"动词的口语并列句:按多任务猜,且必须预览确认(needs_confirm)。"""
    intent = router.route("下午要准备会议论文,另外把WorkBuddy和Codex的使用说明整理一下。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "create_task"
    assert intent.entities.get("needs_confirm") is True


def test_fr08_route_plain_statement_stays_field_note() -> None:
    """无并列线索的纯陈述仍走现场记录(不得扩大写操作误触)。"""
    intent = router.route("把WorkBuddy和Codex的使用说明整理一下。")
    assert intent.command is None
    assert intent.kind == IntentKind.FIELD_NOTE


def test_fr08_route_de_fronted_multi_tasks() -> None:
    """"会议论文得准备,说明文档也要弄一下" → 两项待办预览(无显式创建动词也要识别)。"""
    intent = router.route("会议论文得准备,说明文档也要弄一下。")
    assert intent.command == "create_task"
    assert intent.entities.get("needs_confirm") is True
    assert split_tasks(str(intent.entities["task_title"])) == [
        "准备会议论文",
        "弄说明文档",
    ]


def test_fr08_de_fronted_enters_preview(env: Env) -> None:
    """双前置口语句进入两项预览,确认前不写入。"""
    skill, _, tasks = env
    intent = router.route("会议论文得准备,说明文档也要弄一下。")
    result = skill.execute(intent, "rec-d1")
    assert result.status == "clarify"
    assert "2 个任务" in result.title
    assert "准备会议论文" in (result.body or "")
    assert "弄说明文档" in (result.body or "")
    assert tasks.list_all() == []


# ---------- 预览确认闭环 ----------


def test_fr08_multi_create_previews_then_confirm(env: Env) -> None:
    """≥2 条:先 clarify 预览,不写入;确认(可带编辑)才批量创建。"""
    skill, _, tasks = env
    intent = router.route("下午要准备会议论文,另外把WorkBuddy和Codex的使用说明整理一下。")
    result = skill.execute(intent, "rec-m1")
    assert result.status == "clarify"
    assert "2 个任务" in result.title
    assert "准备会议论文" in (result.body or "")
    assert tasks.list_all() == []  # 确认前不写入

    confirmed = skill.confirm_create(
        "rec-m1",
        CONFIRM_ALL_ID,
        ["准备会议论文(带电脑)", "整理 WorkBuddy 和 Codex 的使用说明"],
    )
    assert confirmed.status == "success"
    assert "已创建 2 个任务" in confirmed.title
    stored = [t["title"] for t in tasks.list_all()]
    assert "准备会议论文(带电脑)" in stored
    assert "整理 WorkBuddy 和 Codex 的使用说明" in stored


def test_fr08_multi_cancel_writes_nothing(env: tuple[TaskCommandSkill, CardRepo, TaskRepo]) -> None:
    skill, _, tasks = env
    intent = router.route("写周报,另外交报销单。")
    assert skill.execute(intent, "rec-m2").status == "clarify"
    cancelled = skill.confirm_create("rec-m2", "task:cancel")
    assert cancelled.status == "success"
    assert "未创建" in cancelled.title
    assert tasks.list_all() == []


def test_fr08_empty_edited_labels_rejected(env: Env) -> None:
    """编辑后标题全空:失败,不得创建空任务。"""
    skill, _, tasks = env
    intent = router.route("写周报,另外交报销单。")
    skill.execute(intent, "rec-m3")
    result = skill.confirm_create("rec-m3", CONFIRM_ALL_ID, ["", "  "])
    assert result.status == "failed"
    assert tasks.list_all() == []


def test_fr08_expired_preview_rejected(env: tuple[TaskCommandSkill, CardRepo, TaskRepo]) -> None:
    skill, _, tasks = env
    result = skill.confirm_create("no-such", CONFIRM_ALL_ID)
    assert result.status == "failed"
    assert tasks.list_all() == []


def test_fr08_single_task_still_direct(env: tuple[TaskCommandSkill, CardRepo, TaskRepo]) -> None:
    """单任务(显式创建动词)保持直接创建,不多走一步确认。"""
    skill, _, tasks = env
    intent = router.route("新建一个任务,明天之前回复客户邮件。")
    result = skill.execute(intent, "rec-m4")
    assert result.status == "success"
    assert "已新建" in result.title
    assert len(tasks.list_all()) == 1


# ---------- desk 完成/取消接口 ----------


def _make_client(tmp_path: Path) -> TestClient:
    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    return TestClient(create_app(config))


def test_fr08_desk_complete_task_syncs_card(tmp_path: Path) -> None:
    """勾选完成:tasks.done、关联卡撤下、工作台显示已完成。"""
    client = _make_client(tmp_path)
    conn = init_db(tmp_path / "agent.db")
    tasks = TaskRepo(conn)
    cards = CardRepo(conn)
    tid = tasks.insert("周报撰写", task_id="t-1")
    cards.upsert("c-1", "task", "周报撰写截止", ref_task_id=tid)

    resp = client.post(f"/desk/tasks/{tid}/complete")
    assert resp.status_code == 200
    assert tasks.get(tid)["status"] == "done"
    assert cards.get("c-1")["status"] == "dismissed"
    shown = [t for t in client.get("/desk/tasks").json() if t["id"] == tid]
    assert shown[0]["status"] == "已完成"
    assert client.post("/desk/tasks/no-such/complete").status_code == 404


def test_fr08_desk_cancel_reminder(tmp_path: Path) -> None:
    """取消提醒:timer 卡撤下(cancelled)、工作台显示已撤下;未知 id 404。"""
    client = _make_client(tmp_path)
    conn = init_db(tmp_path / "agent.db")
    cards = CardRepo(conn)
    cards.upsert("c-t", "timer", "明天十点发周报", remind_at="2026-07-22T10:00:00+08:00")

    resp = client.post("/desk/reminders/c-t/cancel")
    assert resp.status_code == 200
    assert cards.get("c-t")["status"] == "dismissed"
    assert cards.get("c-t")["dismiss_reason"] == "cancelled"
    shown = [t for t in client.get("/desk/tasks").json() if t["id"] == "c-t"]
    assert shown[0]["status"] == "已撤下"
    assert client.post("/desk/reminders/no-such/cancel").status_code == 404
