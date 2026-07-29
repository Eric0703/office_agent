"""FR-04:LLM 分类 + 规则兜底(桩 LLM,确定性;不经任何外部调用)。

判定顺序:显式模式优先 → LLM 分类 → 规则兜底(LLM 异常/非法输出时);
LLM 置信度低于阈值 → unknown(反问,不再走规则);task_command 无指令名时规则补齐,
补不出 → unknown(不猜测执行)。桩记录调用次数,验证 LLM 是否被使用。
"""

from pathlib import Path
from typing import Any

from agent_host.core.processing import process_text
from agent_host.router.router import IntentKind, IntentRouter


class StubLLM:
    """可控桩:固定响应或固定异常;记录 prompt 调用。"""

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls: list[str] = []

    def complete(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(prompt)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return dict(self._response)


def test_fr04_llm_adopts_valid_classification() -> None:
    """LLM 高置信分类被采用(规则本会误判为现场记录的经验句)。"""
    llm = StubLLM({"intent": "experience", "confidence": 0.85, "command": None, "entities": {}})
    router = IntentRouter(llm)
    intent = router.route("下次我们得提前把接口文档冻结,别再临时改。")
    assert intent.kind == IntentKind.EXPERIENCE
    assert intent.confidence == 0.85
    assert len(llm.calls) == 1
    assert "接口文档冻结" in llm.calls[0]  # 分类提示词携带原文


def test_fr04_llm_low_confidence_goes_unknown_without_rules() -> None:
    """置信度低于阈值 → unknown(反问),即使规则能命中也不再走规则。"""
    llm = StubLLM({"intent": "task_command", "confidence": 0.3, "command": None, "entities": {}})
    router = IntentRouter(llm)
    intent = router.route("提醒我明天十点交方案。")
    assert intent.kind == IntentKind.UNKNOWN
    assert intent.confidence == 0.3
    assert intent.command is None


def test_fr04_llm_error_falls_back_to_rules() -> None:
    """LLM 调用异常 → 规则兜底(不影响本地可用性)。"""
    llm = StubLLM(error=RuntimeError("boom"))
    router = IntentRouter(llm)
    intent = router.route("提醒我明天十点交方案。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "create_reminder"


def test_fr04_llm_invalid_intent_falls_back_to_rules() -> None:
    """LLM 输出非法 intent → 规则兜底。"""
    llm = StubLLM({"intent": "party_time", "confidence": 0.9})
    router = IntentRouter(llm)
    intent = router.route("把周报撰写标记为已完成。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "complete_task"


def test_fr04_llm_non_whitelist_command_falls_back_to_rules() -> None:
    """LLM 给白名单外指令名 → 视为非法输出,规则兜底(安全方向)。"""
    llm = StubLLM(
        {"intent": "task_command", "confidence": 0.9, "command": "delete_all", "entities": {}}
    )
    router = IntentRouter(llm)
    intent = router.route("把周报撰写标记为已完成。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "complete_task"  # 规则结果,非 delete_all


def test_fr04_llm_task_command_without_command_filled_by_rules() -> None:
    """LLM 判 task_command 但没给指令名:用规则抽取补齐。"""
    llm = StubLLM({"intent": "task_command", "confidence": 0.9, "command": None, "entities": {}})
    router = IntentRouter(llm)
    intent = router.route("提醒我明天十点交方案。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "create_reminder"
    assert intent.entities["remind_query"]


def test_fr04_llm_task_command_unresolvable_goes_unknown() -> None:
    """LLM 判 task_command,但 LLM 与规则都给不出指令名 → unknown(不猜测执行)。"""
    llm = StubLLM({"intent": "task_command", "confidence": 0.9, "command": None, "entities": {}})
    router = IntentRouter(llm)
    intent = router.route("那个事情你帮我弄一下。")
    assert intent.kind == IntentKind.UNKNOWN


def test_fr04_llm_entities_passthrough() -> None:
    """LLM 给的合法指令与参数原样采用。"""
    llm = StubLLM(
        {
            "intent": "task_command",
            "confidence": 0.92,
            "command": "create_task",
            "entities": {"task_title": "回复客户邮件", "due": "明天"},
        }
    )
    router = IntentRouter(llm)
    intent = router.route("帮我安排明天之前回复客户邮件。")
    assert intent.command == "create_task"
    assert intent.entities == {"task_title": "回复客户邮件", "due": "明天"}


def test_fr04_llm_create_reminder_defaults_query() -> None:
    """create_reminder 缺 remind_query 时以原文兜底。"""
    llm = StubLLM(
        {
            "intent": "task_command",
            "confidence": 0.9,
            "command": "create_reminder",
            "entities": {},
        }
    )
    router = IntentRouter(llm)
    intent = router.route("周五下午提醒我约客户复盘。")
    assert intent.command == "create_reminder"
    assert "周五下午" in intent.entities["remind_query"]


def test_fr04_explicit_mode_skips_llm() -> None:
    """显式模式优先:field/experience 直接采用,LLM 不被调用。"""
    llm = StubLLM({"intent": "task_command", "confidence": 0.99})
    router = IntentRouter(llm)
    assert router.route("新建一个任务测试", mode="field").kind == IntentKind.FIELD_NOTE
    assert router.route("随便一句话", mode="experience").kind == IntentKind.EXPERIENCE
    assert llm.calls == []


def test_fr04_llm_confidence_nan_inf_out_of_range_rejected() -> None:
    """confidence 为 NaN/无穷/越界/bool/字符串:一律视为非法输出,回退规则兜底。"""
    for bad in (float("nan"), float("inf"), -0.1, 1.1, True, "high"):
        llm = StubLLM({"intent": "experience", "confidence": bad})
        router = IntentRouter(llm)
        intent = router.route("刚才会上聊了交付排期和人力安排。")
        # 回退规则:无指令/经验线索 → field_note(规则值 0.5),不得采用 LLM 输出
        assert intent.kind == IntentKind.FIELD_NOTE, bad
        assert intent.confidence == 0.5, bad


def test_fr04_llm_bad_entity_type_falls_back_without_stuck(tmp_path: Path) -> None:
    """task_title 为数组:视为非法输出回退规则;process_text 不把 records 卡在 routed。"""
    from agent_host.adapters.task import MockTaskAdapter
    from agent_host.audit.logger import AuditLogger
    from agent_host.core.processing import ProcessingDeps, process_text
    from agent_host.skills.experience import ExperienceSkill
    from agent_host.skills.field_note import FieldNoteSkill
    from agent_host.skills.reminder import ReminderSkill
    from agent_host.skills.task_command import TaskCommandSkill
    from agent_host.store.db import init_db
    from agent_host.store.repos import (
        AuditRepo,
        CardRepo,
        DeviceRepo,
        DraftRepo,
        RecordRepo,
        TaskRepo,
    )

    conn = init_db(tmp_path / "t.db")
    DeviceRepo(conn).create(
        device_id="dev-t", name="测试设备", token_hash="x", paired_at="2026-01-01T00:00:00+00:00"
    )
    llm = StubLLM(
        {
            "intent": "task_command",
            "confidence": 0.95,
            "command": "create_task",
            "entities": {"task_title": ["回复客户邮件"]},  # 非法:数组
        }
    )
    deps = ProcessingDeps(
        records=RecordRepo(conn),
        router=IntentRouter(llm),
        field_notes=FieldNoteSkill(DraftRepo(conn)),
        experience=ExperienceSkill(DraftRepo(conn)),
        reminders=ReminderSkill(CardRepo(conn), TaskRepo(conn)),
        task_commands=TaskCommandSkill(MockTaskAdapter(TaskRepo(conn)), CardRepo(conn)),
        audit=AuditLogger(AuditRepo(conn)),
        low_confidence_threshold=0.5,
        cache_result=lambda payload: None,
    )
    deps.records.create(
        record_id="rec-bad-entity",
        device_id="dev-t",
        mode="auto",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=3000,
    )
    outcome = process_text(
        deps,
        record_id="rec-bad-entity",
        text="新建任务,明天之前回复客户邮件。",
        confidence=0.99,
        mode="auto",
        device_id="dev-t",
    )
    # 回退规则后正常执行:records 到终态(不得卡在 routed)
    status = conn.execute(
        "SELECT status FROM records WHERE id = 'rec-bad-entity'"
    ).fetchone()[0]
    assert status == "done"
    assert outcome.messages[0].payload["status"] == "success"
    assert "已新建" in outcome.messages[0].payload["title"]


def _make_deps(tmp_path: Path, llm: StubLLM):
    """最小 ProcessingDeps 装配(供 process_text 级回归;临时库)。"""
    from agent_host.adapters.task import MockTaskAdapter
    from agent_host.audit.logger import AuditLogger
    from agent_host.core.processing import ProcessingDeps
    from agent_host.skills.experience import ExperienceSkill
    from agent_host.skills.field_note import FieldNoteSkill
    from agent_host.skills.reminder import ReminderSkill
    from agent_host.skills.task_command import TaskCommandSkill
    from agent_host.store.db import init_db
    from agent_host.store.repos import (
        AuditRepo,
        CardRepo,
        DeviceRepo,
        DraftRepo,
        RecordRepo,
        TaskRepo,
    )

    conn = init_db(tmp_path / "t.db")
    DeviceRepo(conn).create(
        device_id="dev-t", name="测试设备", token_hash="x", paired_at="2026-01-01T00:00:00+00:00"
    )
    deps = ProcessingDeps(
        records=RecordRepo(conn),
        router=IntentRouter(llm),
        field_notes=FieldNoteSkill(DraftRepo(conn)),
        experience=ExperienceSkill(DraftRepo(conn)),
        reminders=ReminderSkill(CardRepo(conn), TaskRepo(conn)),
        task_commands=TaskCommandSkill(MockTaskAdapter(TaskRepo(conn)), CardRepo(conn)),
        audit=AuditLogger(AuditRepo(conn)),
        low_confidence_threshold=0.5,
        cache_result=lambda payload: None,
    )
    return deps, conn


def test_fr04_llm_huge_confidence_no_exception_no_stuck(tmp_path: Path) -> None:
    """confidence 为超大整数(10**1000):不抛异常,回退规则,records 不卡 transcribed/routed。"""
    llm = StubLLM({"intent": "field_note", "confidence": 10**1000})
    deps, conn = _make_deps(tmp_path, llm)
    deps.records.create(
        record_id="rec-huge",
        device_id="dev-t",
        mode="auto",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=3000,
    )
    outcome = process_text(  # 不应抛 OverflowError
        deps,
        record_id="rec-huge",
        text="刚才会上聊了交付排期和人力安排。",
        confidence=0.99,
        mode="auto",
        device_id="dev-t",
    )
    status = conn.execute("SELECT status FROM records WHERE id = 'rec-huge'").fetchone()[0]
    assert status == "done"  # 回退规则 → field_note 草稿完成;不卡 transcribed/routed
    assert outcome.messages[0].payload["status"] == "success"


def test_fr04_llm_entities_list_rejected_rules_extract(tmp_path: Path) -> None:
    """create_task 返回 entities=[]:不采用该输出,回退规则并正确抽取原文任务标题。"""
    llm = StubLLM(
        {
            "intent": "task_command",
            "confidence": 0.95,
            "command": "create_task",
            "entities": [],  # 非法:非 dict
        }
    )
    deps, conn = _make_deps(tmp_path, llm)
    deps.records.create(
        record_id="rec-elist",
        device_id="dev-t",
        mode="auto",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=3000,
    )
    outcome = process_text(
        deps,
        record_id="rec-elist",
        text="新建任务,明天之前回复客户邮件。",
        confidence=0.99,
        mode="auto",
        device_id="dev-t",
    )
    # 回退规则:从原文抽取标题,正常执行成功
    title = outcome.messages[0].payload["title"]
    assert outcome.messages[0].payload["status"] == "success"
    assert "回复客户邮件" in title
    row = conn.execute("SELECT status, intent FROM records WHERE id = 'rec-elist'").fetchone()
    assert tuple(row) == ("done", "create_task")
