"""笔记归档抽象:Mock 实现先行(08 §2;FR-05;宪法第 8 条人工确认后才可归档)。"""

from typing import Protocol


class NotesAdapter(Protocol):
    """笔记适配器协议。"""

    def archive(self, title: str, content_md: str) -> str:
        """归档 Markdown 笔记,返回文件路径;人工确认前不得调用(宪法第 8 条)。"""
        ...


class MockNotesAdapter:
    """内存 Mock:不落盘,返回伪路径。"""

    def __init__(self) -> None:
        self._archived: dict[str, str] = {}

    def archive(self, title: str, content_md: str) -> str:
        path = f"data/notes/{title}.md"
        self._archived[path] = content_md
        return path
