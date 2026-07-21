"""语音指令:参数抽取、歧义候选、幂等执行(FR-08)。

幂等:执行前查 records + audit_log,重复 record_id 直接返回首次结果(08 §3)。
L2 风险指令必须经 security 确认回路(宪法第 5 条);原型期白名单三指令均为 L0/L1。
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from agent_host.adapters.task import TaskAdapter
from agent_host.router.router import Intent, IntentKind
from agent_host.store.repos import CardRepo

_CLARIFY_MAX = 5  # 登记册 §2.3:candidates ≤5 条
# 候选澄清阈值(Gate 0 反馈):相似度只用于产生候选列表,绝不用于直接执行——
# 无法精确/子串命中时一律交端侧确认,避免写操作误触(Owner 指令)
_CANDIDATE_THRESHOLD = 0.5


def _similarity(query: str, title: str) -> float:
    """查询与任务名的相似度:整串与前缀窗口取大者,容忍「周宝 vs 周报撰写」类截断同音错。"""
    full = SequenceMatcher(None, query, title).ratio()
    window = title[: len(query)] if len(title) >= len(query) else title
    head = SequenceMatcher(None, query[: len(window)], window).ratio()
    return max(full, head)


@dataclass(frozen=True)
class ExecutionResult:
    """执行结果,经 intent.result 回送端侧(登记册 §2.3)。"""

    record_id: str
    status: str  # success / failed / clarify(原型期不接 pending_confirm)
    title: str
    body: str | None = None
    candidates: tuple[dict[str, str], ...] = ()
    error_code: str | None = None
    dismissed_card_ids: tuple[str, ...] = ()
    tool: str | None = None  # 实际执行的白名单指令名,供审计


class TaskCommandSkill:
    """execute(intent, record_id) → Result(08 §2);白名单外指令一律拒绝(FR-09)。"""

    def __init__(self, tasks: TaskAdapter, cards: CardRepo) -> None:
        self._tasks = tasks
        self._cards = cards

    def execute(self, intent: Intent, record_id: str) -> ExecutionResult:
        """执行白名单指令;歧义返回 clarify 候选(≤5 条),不猜测执行。"""
        if intent.kind != IntentKind.TASK_COMMAND:
            raise ValueError(f"非任务指令意图:{intent.kind}")
        if intent.command == "complete_task":
            return self._complete(intent, record_id)
        if intent.command == "create_task":
            return self._create(intent, record_id)
        if intent.command == "list_today_tasks":
            return self._list_today(record_id)
        # 白名单外(如 remind/postpone/set_priority):拒绝执行
        return ExecutionResult(
            record_id=record_id,
            status="failed",
            title="暂不支持的指令",
            body="原型期仅支持:完成任务 / 查询今日任务 / 新建任务",
            error_code="INTENT_UNKNOWN",
        )

    def complete_by_id(self, task_id: str, record_id: str) -> ExecutionResult:
        """clarify.select 选定候选后的执行入口(candidate_id 即 task_id)。"""
        return self._complete_one(task_id, record_id)

    def _complete(self, intent: Intent, record_id: str) -> ExecutionResult:
        title_q = intent.entities.get("task_title")
        if not title_q:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="没听清任务名",
                error_code="INTENT_UNKNOWN",
                tool="complete_task",
            )
        open_tasks = self._tasks.list_open()
        # 精确/子串命中:唯一才直接执行;多命中即澄清(登记册 §2.3)
        matches = [t for t in open_tasks if title_q in t.title]
        if len(matches) == 1:
            return self._complete_one(matches[0].id, record_id)
        if len(matches) > 1:
            return self._clarify(record_id, matches, f"找到 {len(matches)} 个匹配任务,请选择")
        # 无法精确匹配:一律返回候选列表交端侧确认,绝不猜测执行(Gate 0 反馈)
        if not open_tasks:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="没有未完成的任务",
                error_code="INTENT_UNKNOWN",
                tool="complete_task",
            )
        scored = sorted(
            ((t, _similarity(title_q, t.title)) for t in open_tasks),
            key=lambda item: item[1],
            reverse=True,
        )
        candidates = [t for t, s in scored if s >= _CANDIDATE_THRESHOLD]
        if not candidates:
            # 完全不相近:列出全部未完成任务供端侧选择,而不是直接失败
            candidates = [t for t, _ in scored]
        return self._clarify(record_id, candidates, "未精确匹配,请确认任务")

    def _clarify(self, record_id: str, tasks: list, title: str) -> ExecutionResult:
        """歧义/不精确命中:不猜测,交端侧选择(登记册 §2.3 clarify)。"""
        return ExecutionResult(
            record_id=record_id,
            status="clarify",
            title=title,
            candidates=tuple(
                {"candidate_id": t.id, "label": t.title} for t in tasks[:_CLARIFY_MAX]
            ),
            tool="complete_task",
        )

    def _complete_one(self, task_id: str, record_id: str) -> ExecutionResult:
        task = self._tasks.complete(task_id)
        dismissed: list[str] = []
        card = self._cards.find_active_by_task(task.id)
        if card is not None:
            # "说完即消"(08 §1.3):语音完成任务 → 对应卡片撤下,由编排层推 reminder.dismiss
            self._cards.dismiss(card["id"], "completed")
            dismissed.append(card["id"])
        return ExecutionResult(
            record_id=record_id,
            status="success",
            title=f"已完成:{task.title}",
            dismissed_card_ids=tuple(dismissed),
            tool="complete_task",
        )

    def _create(self, intent: Intent, record_id: str) -> ExecutionResult:
        title = intent.entities.get("task_title")
        if not title:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="没听清任务内容",
                error_code="INTENT_UNKNOWN",
                tool="create_task",
            )
        task = self._tasks.add(title, due_at=intent.entities.get("due"))
        return ExecutionResult(
            record_id=record_id, status="success", title=f"已新建:{task.title}", tool="create_task"
        )

    def _list_today(self, record_id: str) -> ExecutionResult:
        tasks = self._tasks.list_today()
        body = "\n".join(f"· {t.title}" for t in tasks) if tasks else "无未完成任务"
        return ExecutionResult(
            record_id=record_id,
            status="success",
            title=f"共 {len(tasks)} 项未完成任务",
            body=body,
            tool="list_today_tasks",
        )
