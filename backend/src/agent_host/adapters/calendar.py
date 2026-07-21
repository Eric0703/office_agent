"""日历系统抽象:Mock 实现先行(08 §2;FR-06 简报数据源)。

原型期 Mock 落 SQLite(calendar_events 表,经 CalendarEventRepo),与 mock import 共享数据。
"""

from dataclasses import dataclass
from typing import Protocol

from agent_host.store.repos import CalendarEventRepo


@dataclass(frozen=True)
class CalendarEvent:
    """日历事件(08 §3 calendar_events 表)。"""

    id: str
    title: str
    start_at: str
    end_at: str
    location: str | None = None


class CalendarAdapter(Protocol):
    """日历适配器协议(只读查询)。"""

    def list_events(self, date: str) -> list[CalendarEvent]:
        """按 YYYY-MM-DD 查询当日事件。"""
        ...


class MockCalendarAdapter:
    """SQLite-backed Mock:查 calendar_events 表。"""

    def __init__(self, repo: CalendarEventRepo) -> None:
        self._repo = repo

    def list_events(self, date: str) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                id=r["id"],
                title=r["title"],
                start_at=r["start_at"],
                end_at=r["end_at"],
                location=r["location"],
            )
            for r in self._repo.list_by_date(date)
        ]
