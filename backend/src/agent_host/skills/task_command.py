"""语音指令:参数抽取、歧义候选、幂等执行(FR-08)。

幂等:执行前查 records + audit_log,重复 record_id 直接返回首次结果(08 §3)。
L2 风险指令必须经 security 确认回路(宪法第 5 条);原型期白名单三指令均为 L0/L1。
多任务(2026-07-21):口语并列表达规则切分为多条待办,一律先经可编辑预览确认,
确认前不写入;"和/、"不切分(避免拆散 "WorkBuddy 和 Codex" 这类并列宾语)。
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from agent_host.adapters.task import TaskAdapter
from agent_host.router.router import Intent, IntentKind
from agent_host.store.repos import CardRepo

_CLARIFY_MAX = 5  # 登记册 §2.3:candidates ≤5 条
# 候选澄清阈值(Gate 0 反馈):相似度只用于产生候选列表,绝不用于直接执行——
# 无法精确/子串命中时一律交端侧确认,避免写操作误触(Owner 指令)
_CANDIDATE_THRESHOLD = 0.5

CONFIRM_ALL_ID = "task:confirm_all"
CANCEL_ID = "task:cancel"
_PREVIEW_MAX = 5  # 预览最多 5 条,超出并入最后一条(登记册候选上限对齐)

# 多任务切分:显式并列词与标点;"和"、"、"不切(并列宾语常见,误拆风险高);
# "提前"作切分词(口语子句边界;"提前准备 X"清洗时会被引导词规则剥掉,不误拆)
_SPLIT_RE = re.compile(r"另外|还有|再就是|然后|接着|同时|并且|以及|提前|[,。;,,;]")
# 口语元信息尾注("这是两个任务"等):拆分前剥掉,不作为任务内容
META_TAIL_RE = re.compile(r"这是[两二三四五六七八九\d]+个任务[。！!]?$")
_TIME_WORD_RE = re.compile(
    r"今天|明天|后天|上午|下午|晚上|中午|凌晨|早上|清晨|今晚"
    r"|\d{1,2}[:：]\d{1,2}(?:分)?|[零一二三四五六七八九十两\d]{1,3}点(?:半|[零一二三四五六七八九十两\d]{1,3}分)?"
)
_LEAD_RE = re.compile(r"^(把|要|需要|得|帮我|记得|先|提前)+")
_TRAIL_RE = re.compile(r"(一下|了)$")
_MOVE_VERB_RE = re.compile(
    r"^把(?P<obj>.+?)(?P<verb>整理|准备|完成|处理|回复|发送|提交|写|做|看|买|订|修改|打印|联系|安排|交|发|弄)(?:一下|下)?$"
)
# 宾语前置句:"会议论文得准备"→"准备会议论文";"说明文档也要弄一下"→"弄说明文档"
_FRONTED_RE = re.compile(
    r"^(?P<obj>.{2,}?)(?:得|也要|也)(?P<verb>整理|准备|完成|处理|回复|发送|提交|写|做|看|买|订|修改|打印|联系|安排|交|发|弄|学习)(?:一下|下)?$"
)
# 双前置并列句:"会议论文得准备,说明文档也要弄一下"(标点被 ASR 归一化吃掉时的兜底)
_DOUBLE_FRONTED_RE = re.compile(
    r"^(?P<o1>.{2,}?)得(?P<v1>整理|准备|完成|处理|回复|发送|提交|写|做|看|买|订|修改|打印|联系|安排|交|发|弄|学习)"
    r"(?P<o2>.{2,}?)(?:也要|也得|还要|还得)(?P<v2>整理|准备|完成|处理|回复|发送|提交|写|做|看|买|订|修改|打印|联系|安排|交|发|弄|学习)(?:一下|下)?$"
)


def split_tasks(text: str) -> list[str]:
    """口语文本 → 多条待办标题(规则切分 + 逐条清洗;≤5 条,超出并入末条)。

    清洗:剥元信息尾注/时间词/语气词;"把 X 整理一下"→"整理 X"、"X 得准备"→"准备 X";
    不确定的一律原样保留,是否采信由预览确认决定(确认前不写入)。
    """
    text = META_TAIL_RE.sub("", text)
    double = _DOUBLE_FRONTED_RE.match(text)
    if double:
        # "X 得准备,Y 也要弄" → 两条动宾(标点被归一化吞掉时的结构兜底)
        return [
            f"{double.group('v1')}{double.group('o1')}",
            f"{double.group('v2')}{double.group('o2')}",
        ]
    titles: list[str] = []
    for seg in _SPLIT_RE.split(text):
        title = clean_segment(seg)
        if title:
            titles.append(title)
    if len(titles) > _PREVIEW_MAX:
        titles = [*titles[: _PREVIEW_MAX - 1], "、".join(titles[_PREVIEW_MAX - 1 :])]
    return titles


def clean_segment(seg: str) -> str | None:
    """单段清洗:去时间词;"把字句/宾语前置"还原为动宾;剥引导/尾随语气词。"""
    seg = _TIME_WORD_RE.sub(" ", seg).strip(" ,。,;、")
    moved = _MOVE_VERB_RE.match(seg) or _FRONTED_RE.match(seg)
    if moved:
        return f"{moved.group('verb')}{moved.group('obj')}".strip()
    seg = _LEAD_RE.sub("", seg).strip(" ,。,;、")
    if not seg:
        return None
    seg = _TRAIL_RE.sub("", seg).strip(" ,。,;、")
    return seg or None


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
    """execute(intent, record_id) → Result(08 §2);白名单外指令一律拒绝(FR-09)。

    多任务预览:_create 检出 ≥2 条(或口语猜测)时挂起待确认预览,
    经 clarify.select(task:confirm_all/task:cancel,可带 edited_labels)终局;
    确认前不产生任何写入(Owner 指令:不猜测执行)。
    """

    def __init__(self, tasks: TaskAdapter, cards: CardRepo) -> None:
        self._tasks = tasks
        self._cards = cards
        self._pending_preview: dict[str, tuple[list[str], str | None]] = {}

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
        # 白名单外(如 postpone/set_priority/cancel_reminder):拒绝执行
        return ExecutionResult(
            record_id=record_id,
            status="failed",
            title="暂不支持的指令",
            body="当前仅支持:完成任务 / 查询今日任务 / 新建任务 / 新建提醒",
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
        raw_title = intent.entities.get("task_title")
        if not raw_title:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="没听清任务内容",
                error_code="INTENT_UNKNOWN",
                tool="create_task",
            )
        due = intent.entities.get("due")
        titles = split_tasks(raw_title)
        # 多任务或口语猜测:先预览确认,不直接创建(FR-08 补充,2026-07-21)
        if len(titles) >= 2 or intent.entities.get("needs_confirm"):
            if not titles:
                return ExecutionResult(
                    record_id=record_id,
                    status="failed",
                    title="没听清任务内容",
                    error_code="INTENT_UNKNOWN",
                    tool="create_task",
                )
            self._pending_preview[record_id] = (titles, due)
            preview = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1))
            return ExecutionResult(
                record_id=record_id,
                status="clarify",
                title=f"听出 {len(titles)} 个任务,请确认",
                body=preview,
                candidates=(
                    {"candidate_id": CONFIRM_ALL_ID, "label": "全部创建"},
                    {"candidate_id": CANCEL_ID, "label": "取消"},
                ),
                tool="create_task",
            )
        task = self._tasks.add(titles[0] if titles else raw_title, due_at=due)
        return ExecutionResult(
            record_id=record_id, status="success", title=f"已新建:{task.title}", tool="create_task"
        )

    def cancel_pending(self, record_id: str) -> ExecutionResult:
        """task:cancel 统一取消(歧义 clarify 与多任务预览通用):不执行任何候选。

        预览挂起弹掉(歧义 clarify 本无挂起态);终态落库/缓存/审计由编排层负责。
        """
        self._pending_preview.pop(record_id, None)
        return ExecutionResult(record_id=record_id, status="success", title="已取消")

    def confirm_create(
        self, record_id: str, candidate_id: str, edited_labels: list[str] | None = None
    ) -> ExecutionResult:
        """多任务预览终局:确认(可带编辑后标题)才批量创建;取消/过期不产生任务。"""
        pending = self._pending_preview.pop(record_id, None)
        if pending is None:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="该确认已过期,请重新说",
                error_code="INTENT_UNKNOWN",
                tool="create_task",
            )
        titles, due = pending
        if candidate_id == CANCEL_ID:
            return ExecutionResult(
                record_id=record_id,
                status="success",
                title="已取消,未创建任务",
                tool="create_task",
            )
        if candidate_id != CONFIRM_ALL_ID:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="未知的选择,请重新说",
                error_code="INTENT_UNKNOWN",
                tool="create_task",
            )
        if edited_labels is not None:
            titles = [t.strip() for t in edited_labels if t.strip()][:_PREVIEW_MAX]
        if not titles:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="没有可创建的任务",
                error_code="INTENT_UNKNOWN",
                tool="create_task",
            )
        created = [self._tasks.add(title, due_at=due) for title in titles]
        return ExecutionResult(
            record_id=record_id,
            status="success",
            title=f"已创建 {len(created)} 个任务",
            body="\n".join(f"· {t.title}" for t in created),
            tool="create_task",
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
