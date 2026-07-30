"""笔记归档抽象:Mock 实现先行(08 §2;FR-05;宪法第 8 条人工确认后才可归档)。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class NotesAdapter(Protocol):
    """笔记适配器协议。"""

    def archive(self, title: str, content_md: str, draft_id: str) -> str:
        """归档 Markdown 笔记,返回文件路径;人工确认前不得调用(宪法第 8 条)。"""
        ...


class MockNotesAdapter:
    """内存 Mock:不落盘,返回伪路径(按 draft_id 区分,连续归档不互相覆盖)。"""

    def __init__(self) -> None:
        self._archived: dict[str, str] = {}

    def archive(self, title: str, content_md: str, draft_id: str) -> str:
        path = f"data/notes/{title}-{draft_id}.md"
        self._archived[path] = content_md
        return path


class LocalNotesAdapter:
    """本机文件归档:写入配置的 notes_dir(宪法第 3 条本地优先)。

    文件名由确认日期与系统生成的完整 draft id 构成(YYYYMMDD-<draft_id>.md),
    不使用用户正文生成路径;不做版本管理、检索或通用存储层。
    """

    def __init__(self, notes_dir: str) -> None:
        self._dir = Path(notes_dir)

    def archive(self, title: str, content_md: str, draft_id: str) -> str:
        """把草稿正文原样写入 notes_dir,返回文件路径。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(UTC).strftime("%Y%m%d")
        path = self._dir / f"{day}-{draft_id}.md"
        path.write_text(content_md, encoding="utf-8")
        return str(path)
