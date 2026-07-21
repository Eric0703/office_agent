"""每日简报:定时/补发生成、条目来源追溯(FR-06;登记册 §2.4 brief.push ≤5 条)。"""

from dataclasses import dataclass, field

from agent_host.adapters.calendar import CalendarAdapter
from agent_host.adapters.task import TaskAdapter


@dataclass(frozen=True)
class BriefingItem:
    """简报条目;kind ∈ event/task/conflict,source_id 指向来源 task/event(可追溯)。"""

    kind: str
    title: str
    source_id: str
    time: str | None = None


@dataclass(frozen=True)
class Briefing:
    """一日简报(date 为 YYYY-MM-DD)。"""

    date: str
    items: list[BriefingItem] = field(default_factory=list)


class BriefingSkill:
    """generate(date) → Briefing(08 §2)。"""

    def __init__(self, tasks: TaskAdapter, calendar: CalendarAdapter) -> None:
        self._tasks = tasks
        self._calendar = calendar

    def generate(self, date: str) -> Briefing:
        """汇总当日任务与日历事件生成简报;条目级来源可追溯(FR-06 验收)。"""
        raise NotImplementedError
