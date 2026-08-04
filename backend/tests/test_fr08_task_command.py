"""FR-08:语音指令执行 —— 完成任务并"说完即消"(08 §1.3)、同名歧义 clarify。"""

from pathlib import Path

import pytest

from agent_host.adapters.task import MockTaskAdapter
from agent_host.router.router import IntentRouter
from agent_host.skills.task_command import TaskCommandSkill
from agent_host.store.db import init_db
from agent_host.store.repos import CardRepo, TaskRepo


@pytest.fixture
def env(tmp_path: Path) -> tuple[TaskCommandSkill, CardRepo, TaskRepo]:
    conn = init_db(tmp_path / "t.db")
    tasks = TaskRepo(conn)
    cards = CardRepo(conn)
    tasks.insert("周报撰写", task_id="t-weekly")
    tasks.insert("周报汇总", task_id="t-collect")
    cards.upsert("c-weekly", "task", "周报撰写截止", ref_task_id="t-weekly")
    return TaskCommandSkill(MockTaskAdapter(tasks), cards), cards, tasks


def test_fr08_complete_task_dismisses_card(
    env: tuple[TaskCommandSkill, CardRepo, TaskRepo],
) -> None:
    skill, cards, tasks = env
    intent = IntentRouter().route("把周报撰写标记为已完成。")
    result = skill.execute(intent, "rec-1")
    assert result.status == "success"
    assert tasks.get("t-weekly")["status"] == "done"
    card = cards.get("c-weekly")
    assert card["status"] == "dismissed"
    assert card["dismiss_reason"] == "completed"
    assert result.dismissed_card_ids == ("c-weekly",)


def test_fr08_ambiguous_title_returns_clarify(
    env: tuple[TaskCommandSkill, CardRepo, TaskRepo],
) -> None:
    skill, _, tasks = env
    intent = IntentRouter().route("把周报标记为已完成。")
    result = skill.execute(intent, "rec-2")
    assert result.status == "clarify"
    assert len(result.candidates) == 2
    # 歧义不猜测执行:两个任务都仍是 open
    assert tasks.get("t-weekly")["status"] == "open"
    assert tasks.get("t-collect")["status"] == "open"


def test_fr08_create_then_list_today(
    env: tuple[TaskCommandSkill, CardRepo, TaskRepo],
) -> None:
    skill, _, _ = env
    router = IntentRouter()
    created = skill.execute(router.route("新建一个任务,今天之前回复客户邮件。"), "rec-3")
    assert created.status == "success"
    listed = skill.execute(router.route("查一下还有哪些没完成的任务。"), "rec-4")
    assert listed.status == "success"
    assert "回复客户邮件" in (listed.body or "")


def test_fr08_asr_inexact_returns_clarify_candidates(
    env: tuple[TaskCommandSkill, CardRepo, TaskRepo],
) -> None:
    # Gate 0 反馈:同音/截断误转写不精确命中时,返回候选澄清而非失败;
    # 且绝不猜测执行,任务保持 open(Owner 指令:不扩大写操作误触)
    skill, _, tasks = env
    intent = IntentRouter().route("把周报转写标记位已完成。")
    assert intent.command == "complete_task"
    result = skill.execute(intent, "rec-6")
    assert result.status == "clarify"
    labels = [c["label"] for c in result.candidates]
    assert "周报撰写" in labels
    assert tasks.get("t-weekly")["status"] == "open"
    assert tasks.get("t-collect")["status"] == "open"


def test_fr08_inexact_homophone_title_returns_candidates(
    env: tuple[TaskCommandSkill, CardRepo, TaskRepo],
) -> None:
    # Gate 0 反馈:「周宝」误转写应至少列出「周报撰写 / 周报汇总」供确认
    skill, _, tasks = env
    intent = IntentRouter().route("把周宝标记为已完成。")
    result = skill.execute(intent, "rec-7")
    assert result.status == "clarify"
    labels = [c["label"] for c in result.candidates]
    assert "周报撰写" in labels
    assert "周报汇总" in labels
    assert tasks.get("t-weekly")["status"] == "open"


def test_fr08_unrelated_title_lists_all_open(
    env: tuple[TaskCommandSkill, CardRepo, TaskRepo],
) -> None:
    # 完全不相近:列出全部未完成任务供选择,而不是直接失败
    skill, _, tasks = env
    intent = IntentRouter().route("把西瓜土豆标记为已完成。")
    result = skill.execute(intent, "rec-8")
    assert result.status == "clarify"
    assert len(result.candidates) == 2
    assert tasks.get("t-weekly")["status"] == "open"


def test_fr08_unsupported_command_rejected(
    env: tuple[TaskCommandSkill, CardRepo, TaskRepo],
) -> None:
    from agent_host.router.router import Intent, IntentKind

    skill, _, _ = env
    intent = Intent(kind=IntentKind.TASK_COMMAND, command="delete_all")  # 白名单外
    result = skill.execute(intent, "rec-5")
    assert result.status == "failed"
    assert result.error_code == "INTENT_UNKNOWN"
