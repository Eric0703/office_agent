"""任务系统抽象:Mock 实现先行,真实实现后插(08 §2;FR-06/08)。

原型期 Mock 落 SQLite(tasks 表,经 TaskRepo),使 `agent-host mock import` 与 serve
两个进程共享同一份数据;真实办公系统接入时以独立适配器替换,接口不变。
"""

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from agent_host.store.repos import TaskRepo


@dataclass(frozen=True)
class Task:
    """任务;真实系统接入后以 source/source_id 做外部映射(08 §3 tasks 表)。"""

    id: str
    title: str
    status: str = "open"  # open / done
    due_at: str | None = None


class TaskAdapter(Protocol):
    """任务适配器协议(增删查、完成)。"""

    def list_today(self) -> list[Task]:
        """今日任务(简报数据源)。"""
        ...

    def list_open(self) -> list[Task]:
        """全部未完成任务。"""
        ...

    def add(self, title: str, due_at: str | None = None) -> Task:
        """新建任务,返回含 id 的任务。"""
        ...

    def complete(self, task_id: str) -> Task:
        """标记完成。"""
        ...


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(id=row["id"], title=row["title"], status=row["status"], due_at=row["due_at"])


class MockTaskAdapter:
    """SQLite-backed Mock:数据即 tasks 表(source='mock'),保持接口极简。"""

    def __init__(self, repo: TaskRepo) -> None:
        self._repo = repo

    def list_today(self) -> list[Task]:
        """原型期等同 list_open(未接真实日历截止语义)。"""
        return self.list_open()

    def list_open(self) -> list[Task]:
        return [_row_to_task(r) for r in self._repo.list_open()]

    def add(self, title: str, due_at: str | None = None) -> Task:
        task_id = self._repo.insert(title=title, due_at=due_at)
        return Task(id=task_id, title=title, due_at=due_at)

    def complete(self, task_id: str) -> Task:
        self._repo.mark_done(task_id, completed_via="voice")
        row = self._repo.get(task_id)
        if row is None:
            raise KeyError(task_id)
        return _row_to_task(row)
