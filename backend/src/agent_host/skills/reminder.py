"""提醒调度:一次性定时提醒的解析/创建/确认(FR-07;宪法第 4 条卡片仅 task/timer 两类)。

产品定义(Owner 决策,2026-07-21):"定时任务" = 一次性、可取消的定时提醒(timer 卡),
不是周期性 cron;周期重复提醒不在本轮范围。

安全语义:解析明确(日期+时间+内容)才直接创建;缺日期或时间已过等不确定情形,
一律经 clarify 候选确认,确认前不写入任何数据;缺时间/缺内容直接失败提示,
不猜测执行。热词只改善 ASR 识别,不改变本层的参数校验与确认语义。
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from agent_host.skills.task_command import _SPLIT_RE as _SEGMENT_RE  # 同包复用切分规则
from agent_host.skills.task_command import (
    META_TAIL_RE,
    ExecutionResult,
    clean_segment,
)
from agent_host.store.repos import CardRepo, TaskRepo, insert_reminder_with_tasks

CONFIRM_ID = "remind:confirm"
CANCEL_ID = "remind:cancel"

_DATE_RE = re.compile(r"今天|明天|后天")
_CLOCK_RE = re.compile(r"(?P<hour>\d{1,2})[:：](?P<minute>\d{1,2})(?:分)?")
_HOUR_RE = re.compile(
    r"(?P<qual>上午|下午|晚上|中午|凌晨|早上|清晨)?"
    r"(?P<hour>[零一二三四五六七八九十两]{1,3}|\d{1,2})点"
    r"(?P<minute>半|[零一二三四五六七八九十两]{1,3}分|\d{1,2}分)?"
)
_DELTA_RE = re.compile(r"(?P<n>[零一二三四五六七八九十两]{1,3}|\d+)个?(?P<unit>分钟|小时)后")
_CUE_RE = re.compile(r"提醒我|定时提醒|定时任务|提醒|创建|新建|帮我|一个|一下|把")
_STRIP_RE = re.compile(r"[,。,!?;、\s]+")

_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_int(text: str) -> int | None:
    """中文/阿拉伯数字 → int;支持 十/十一/二十/二十五 式。无法解析返回 None。"""
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if len(text) == 2 and text.startswith("十"):
        return 10 + _CN_NUM.get(text[1], -99)
    if len(text) == 2 and text.endswith("十"):
        return _CN_NUM.get(text[0], -99) * 10
    if len(text) == 3 and text[1] == "十":
        return _CN_NUM.get(text[0], -99) * 10 + _CN_NUM.get(text[2], -99)
    return _CN_NUM.get(text)


def _shift(hour: int, qual: str | None) -> int | None:
    """按上午/下午/晚上等限定词折算 24 小时制;超出 0~23 视为无法解析。"""
    if qual in ("下午", "晚上", "中午") and hour < 12:
        hour += 12
    if not 0 <= hour <= 23:
        return None
    return hour


@dataclass(frozen=True)
class RemindParse:
    """提醒解析结果;uncertain=True 时必须经端侧确认才允许写入。"""

    remind_at: datetime | None
    content: str | None
    uncertain: bool = False
    note: str = ""  # 不确定原因(候选确认文案用)


def parse_remind(text: str, now: datetime) -> RemindParse:
    """从归一化文本解析 (提醒时间, 内容, 是否需确认)。

    支持:今天/明天/后天 + (上午/下午/晚上/中午/凌晨)N点(半/N分)、HH:MM、N分钟/小时后。
    """
    spans: list[tuple[int, int]] = []
    target: datetime | None = None
    uncertain = False
    note = ""

    delta = _DELTA_RE.search(text)
    clock = _CLOCK_RE.search(text)
    hour = _HOUR_RE.search(text)
    date = _DATE_RE.search(text)

    if delta:
        n = _cn_int(delta.group("n"))
        if n is not None:
            if delta.group("unit") == "分钟":
                target = now + timedelta(minutes=n)
            else:
                target = now + timedelta(hours=n)
            spans.append(delta.span())
    elif clock or hour:
        if clock:
            hh, mm = int(clock.group("hour")), int(clock.group("minute"))
            spans.append(clock.span())
        else:
            assert hour is not None
            raw_h = _cn_int(hour.group("hour"))
            hh = _shift(raw_h, hour.group("qual")) if raw_h is not None else None
            mm = 0
            raw_m = hour.group("minute")
            if raw_m == "半":
                mm = 30
            elif raw_m:
                mm = _cn_int(raw_m.rstrip("分")) or 0
            spans.append(hour.span())
        if hh is not None and 0 <= mm <= 59:
            base = now
            if date:
                spans.append(date.span())
                offset = {"今天": 0, "明天": 1, "后天": 2}[date.group(0)]
                base = now + timedelta(days=offset)
            else:
                # 缺日期:先按今天,已过则顺延明天,需确认(08 §1.2 不猜测执行)
                uncertain = True
                note = "未听清日期"
            candidate = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now:
                candidate = candidate + timedelta(days=1)
                uncertain = True
                note = note or "原定时间已过,已按明天"
            target = candidate

    # 内容 = 去掉时间/日期片段与指令词后的余文
    content_text = text
    for start, end in sorted(spans, reverse=True):
        content_text = content_text[:start] + " " + content_text[end:]
    content_text = _CUE_RE.sub(" ", content_text)
    content = _STRIP_RE.sub(" ", content_text).strip() or None
    return RemindParse(remind_at=target, content=content, uncertain=uncertain, note=note)


def display_remind(remind_at: datetime, now: datetime, content: str) -> str:
    """"明天 10:00 给 WorkBuddy 发周报" 式的用户可读摘要。"""
    days = (remind_at.date() - now.date()).days
    label = {0: "今天", 1: "明天", 2: "后天"}.get(days) or f"{remind_at.month}月{remind_at.day}日"
    return f"{label} {remind_at:%H:%M} {content}"


@dataclass(frozen=True)
class _Pending:
    """clarify 挂起的待确认内容:单一提醒,或"提醒 + 多任务"复合(tasks 非空)。"""

    reminder: RemindParse | None
    tasks: list[str] = field(default_factory=list)
    now: datetime | None = None


_REMIND_HEAD_RE = re.compile(r"提醒我|提醒|定时")


def _parse_compound(text: str, now: datetime) -> _Pending | None:
    """"提醒我明天九点半开会,提前准备 X,然后整理 Y,这是两个任务" → 提醒 + 多任务。

    仅当头段是完整提醒(含明确时间)且后续段能切出任务时生效;
    否则返回 None,交原单提醒/多任务流程,不强行复合(避免误合并)。
    """
    stripped = META_TAIL_RE.sub("", text)
    segments = [s for s in _SEGMENT_RE.split(stripped) if s.strip(" ,。,;、")]
    if len(segments) < 2 or not _REMIND_HEAD_RE.search(segments[0]):
        return None
    reminder = parse_remind(segments[0], now)
    if reminder.remind_at is None or not reminder.content:
        return None
    tasks = [t for t in (clean_segment(s) for s in segments[1:]) if t]
    if not tasks:
        return None
    return _Pending(reminder=reminder, tasks=tasks, now=now)


class ReminderSkill:
    """一次性定时提醒:execute(直建/澄清/失败)与 confirm_pending(确认后写入)。

    支持"提醒 + 多任务"复合句:头段为完整提醒、后续为任务段时,一次性预览确认。
    待确认解析仅存内存(与 record_id 绑定),确认/取消即清除;进程重启不影响数据安全——
    重启前未确认的解析自然失效,不产生任何写入。
    """

    def __init__(self, cards: CardRepo, tasks: TaskRepo | None = None) -> None:
        self._cards = cards
        self._tasks = tasks  # 复合确认时批量落任务;仅提醒的用法可不传
        self._pending: dict[str, _Pending] = {}

    def execute(
        self, intent_query: str, record_id: str, *, now: datetime | None = None
    ) -> ExecutionResult:
        """解析并分流:复合→预览;明确→直接创建;不确定→clarify 候选;缺要素→失败。"""
        now = now or datetime.now().astimezone()
        compound = _parse_compound(intent_query, now)
        if compound is not None:
            self._pending[record_id] = compound
            rem = compound.reminder
            assert rem is not None and rem.remind_at is not None
            shown = display_remind(rem.remind_at, now, rem.content or "")
            lines = [
                f"提醒:{shown}",
                *(f"任务 {i}:{t}" for i, t in enumerate(compound.tasks, 1)),
            ]
            return ExecutionResult(
                record_id=record_id,
                status="clarify",
                title=f"请确认 1 个提醒和 {len(compound.tasks)} 个任务",
                body="\n".join(lines),
                candidates=(
                    {"candidate_id": CONFIRM_ID, "label": "全部创建"},
                    {"candidate_id": CANCEL_ID, "label": "取消"},
                ),
                tool="create_reminder",
            )
        parsed = parse_remind(intent_query, now)
        if not parsed.content:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="没听清提醒内容",
                error_code="INTENT_UNKNOWN",
                tool="create_reminder",
            )
        if parsed.remind_at is None:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="没听清提醒时间,请说明今天/明天和具体时间",
                error_code="INTENT_UNKNOWN",
                tool="create_reminder",
            )
        if parsed.uncertain:
            self._pending[record_id] = _Pending(reminder=parsed, now=now)
            shown = display_remind(parsed.remind_at, now, parsed.content)
            prefix = f"{parsed.note}," if parsed.note else ""
            return ExecutionResult(
                record_id=record_id,
                status="clarify",
                title=f"{prefix}请确认提醒时间",
                candidates=(
                    {"candidate_id": CONFIRM_ID, "label": f"创建:{shown}"},
                    {"candidate_id": CANCEL_ID, "label": "取消"},
                ),
                tool="create_reminder",
            )
        self._create_card(parsed)
        return ExecutionResult(
            record_id=record_id,
            status="success",
            title=f"已创建提醒:{display_remind(parsed.remind_at, now, parsed.content)}",
            tool="create_reminder",
        )

    def confirm_pending(self, record_id: str, candidate_id: str) -> ExecutionResult:
        """clarify.select 终态:确认才写入;取消/过期不产生任何任务或提醒。

        复合确认(提醒 + 多任务)经 store 层单事务落库(insert_reminder_with_tasks):
        任一写入失败整体回滚、异常上抛,pending 保留可重试;全部成功提交后才清除 pending。
        """
        pending = self._pending.get(record_id)
        if pending is None:
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="该确认已过期,请重新说",
                error_code="INTENT_UNKNOWN",
                tool="create_reminder",
            )
        if candidate_id == CANCEL_ID:
            self._pending.pop(record_id, None)
            return ExecutionResult(
                record_id=record_id,
                status="success",
                title="已取消,未创建提醒或任务" if pending.tasks else "已取消,未创建提醒",
                tool="create_reminder",
            )
        if candidate_id != CONFIRM_ID:
            self._pending.pop(record_id, None)
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="未知的选择,请重新说",
                error_code="INTENT_UNKNOWN",
                tool="create_reminder",
            )
        if pending.tasks and self._tasks is None:
            # 复合确认需要任务落库;装配层必须注入 TaskRepo(防御,正常不会发生)
            return ExecutionResult(
                record_id=record_id,
                status="failed",
                title="当前无法创建任务,请稍后再试",
                error_code="INTERNAL",
                tool="create_reminder",
            )
        now = pending.now or datetime.now().astimezone()
        shown = ""
        if pending.reminder is not None and pending.tasks:
            # 复合:timer 卡 + 全部任务同一事务;失败整体回滚,pending 保留(上面未 pop)
            assert self._tasks is not None
            assert pending.reminder.remind_at is not None
            assert pending.reminder.content is not None
            insert_reminder_with_tasks(
                self._cards,
                self._tasks,
                uuid.uuid4().hex,
                pending.reminder.content,
                pending.reminder.remind_at.isoformat(),
                pending.tasks,
            )
            shown = display_remind(pending.reminder.remind_at, now, pending.reminder.content)
        elif pending.reminder is not None:
            assert pending.reminder.remind_at is not None
            self._create_card(pending.reminder)  # 单提醒:单卡写入;失败同样保留 pending
            shown = display_remind(
                pending.reminder.remind_at, now, pending.reminder.content or ""
            )
        self._pending.pop(record_id, None)  # 全部成功提交后才清除 pending
        if pending.reminder is not None and pending.tasks:
            title = f"已创建:{shown} 和 {len(pending.tasks)} 个任务"
        elif pending.reminder is not None:
            title = f"已创建提醒:{shown}"
        else:
            title = f"已创建 {len(pending.tasks)} 个任务"
        return ExecutionResult(
            record_id=record_id,
            status="success",
            title=title,
            tool="create_reminder",
        )

    def _create_card(self, parsed: RemindParse) -> str:
        """写入 timer 提醒卡(SQLite;remind_at 为明确 ISO8601 时间,非"明天"字符串)。"""
        assert parsed.remind_at is not None and parsed.content is not None
        card_id = uuid.uuid4().hex
        self._cards.upsert(
            card_id,
            "timer",
            parsed.content,
            remind_at=parsed.remind_at.isoformat(),
        )
        return card_id
