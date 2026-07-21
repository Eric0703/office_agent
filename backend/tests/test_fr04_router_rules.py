"""FR-04:关键词规则路由(Mock LLM)单元测试 —— 白名单指令识别与显式模式优先。"""

from agent_host.router.router import IntentKind, IntentRouter

router = IntentRouter()


def test_fr04_complete_task() -> None:
    intent = router.route("把周报撰写标记为已完成。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "complete_task"
    assert intent.entities["task_title"] == "周报撰写"


def test_fr04_create_task_extracts_title_and_due() -> None:
    intent = router.route("新建一个任务,明天之前回复客户邮件。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "create_task"
    assert intent.entities["task_title"] == "回复客户邮件"
    assert intent.entities["due"] == "明天"


def test_fr04_list_today_tasks() -> None:
    intent = router.route("查一下我这周还有哪些没完成的任务。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "list_today_tasks"


def test_fr04_experience() -> None:
    intent = router.route("这次项目延期的根因是需求变更没有走评审,以后任何变更必须先过评审会。")
    assert intent.kind == IntentKind.EXPERIENCE


def test_fr04_field_note_is_auto_default() -> None:
    intent = router.route("刚才聊的那个方案,我觉得核心是交付周期,成本反而是其次的。")
    assert intent.kind == IntentKind.FIELD_NOTE


def test_fr04_explicit_mode_wins() -> None:
    # 显式模式优先:即使文本像指令,field/experience 模式直接采用(08 §1.2)
    assert router.route("新建一个任务测试", mode="field").kind == IntentKind.FIELD_NOTE
    assert router.route("随便一句话", mode="experience").kind == IntentKind.EXPERIENCE


def test_fr04_list_query_asr_variants() -> None:
    # Gate 0 反馈:「查一下」被转写为「插一下/茶一下」时,只读查询仍应命中(L0 保守变体)
    intent = router.route("插一下还有哪些没完成的任务。")
    assert intent.kind == IntentKind.TASK_COMMAND
    assert intent.command == "list_today_tasks"


def test_fr04_no_variant_expansion_for_write_cues() -> None:
    # 写操作不享受变体扩展(Owner 指令):「标己完成」不是合法完成指令,不得误触写操作
    intent = router.route("把周报标己完成。")
    assert intent.kind != IntentKind.TASK_COMMAND
    assert intent.command is None


def test_fr04_unknown_on_empty_transcript() -> None:
    assert router.route(" ,。").kind == IntentKind.UNKNOWN
