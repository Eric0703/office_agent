"""现场记录:结构化笔记草稿、待确认队列、归档(FR-05)。

宪法第 8 条:人工确认前产出物一律是草稿;草稿确认后才经 NotesAdapter 归档。
原型期(Owner 决策)不接 LLM:模板 + 抽取式填充;草稿正文面向用户,不含内部术语。
"""

import re
from dataclasses import dataclass

from agent_host.adapters.notes import MockNotesAdapter, NotesAdapter
from agent_host.store.repos import DraftRepo, TaskDraftRepo

_SENT_SPLIT = re.compile(r"[。!?;!?;\n]+")
_CONCLUSION_CUES = ("所以", "因此", "决定", "达成一致", "结论")
_TODO_CUES = ("需要", "待办", "下一步", "尽快", "务必", "记得")
_TODO_PLACEHOLDERS = ("(无明确待办)", "(待人工补充)")


def extract_todo_titles(content_md: str) -> list[str]:
    """从固定四段模板正文提取"## 待办"章节的项目符号标题(FR-05 待办转任务草稿)。

    只认"## 待办"章节内的 "- " 行,不误取背景/要点/结论;跳过占位行。
    """
    lines = content_md.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## 待办") + 1
    except StopIteration:
        return []
    titles: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("- "):
            title = stripped[2:].strip()
            if title and title not in _TODO_PLACEHOLDERS:
                titles.append(title)
    return titles


@dataclass(frozen=True)
class NoteDraft:
    """笔记草稿(对应 drafts 表 kind='note')。"""

    id: str
    record_id: str
    content_md: str
    status: str = "pending"  # pending / confirmed / discarded


def _pick(sentences: list[str], cues: tuple[str, ...]) -> list[str]:
    return [s for s in sentences if any(c in s for c in cues)]


def _render(record_id: str, transcript: str) -> str:
    """四段模板:背景/要点/结论/待办,全部抽取式填充。"""
    sentences = [s.strip() for s in _SENT_SPLIT.split(transcript) if s.strip()]
    background = sentences[0] if sentences else "(无有效转写内容)"
    points = sentences[1:] if len(sentences) > 1 else sentences
    conclusions = _pick(sentences, _CONCLUSION_CUES)
    todos = _pick(sentences, _TODO_CUES)
    lines = [
        "# 现场记录",
        "",
        "## 背景",
        background,
        "",
        "## 要点",
        *(f"- {p}" for p in points),
        "",
        "## 结论",
        *(conclusions or ["(待人工补充)"]),
        "",
        "## 待办",
        *(f"- {t}" for t in (todos or ["(无明确待办)"])),
        "",
    ]
    return "\n".join(lines)


class FieldNoteSkill:
    """process(record) → Draft(08 §2)。"""

    def __init__(
        self,
        drafts: DraftRepo,
        notes: NotesAdapter | None = None,
        task_drafts: TaskDraftRepo | None = None,
    ) -> None:
        """notes/task_drafts 缺省为 None(存量装配/单测不触归档路径);生产由 app 装配。"""
        self._drafts = drafts
        self._notes = notes if notes is not None else MockNotesAdapter()
        self._task_drafts = task_drafts

    def process(self, record_id: str, transcript: str) -> NoteDraft:
        """转写文本 → 结构化笔记草稿,写入 drafts 表(pending);音频由 audio 管线即删。"""
        content_md = _render(record_id, transcript)
        draft_id = self._drafts.create(record_id=record_id, kind="note", content_md=content_md)
        return NoteDraft(id=draft_id, record_id=record_id, content_md=content_md)

    def archive(self, draft_id: str) -> str:
        """人工确认后归档为 Markdown,返回文件路径(宪法第 8 条)。

        仅接受:存在的草稿、kind='note'、status='pending';
        不存在 → KeyError;非笔记草稿或已非 pending(含重复确认)→ ValueError。
        """
        row = self._drafts.get(draft_id)
        if row is None:
            raise KeyError(draft_id)
        if row["kind"] != "note" or row["status"] != "pending":
            raise ValueError(draft_id)
        path = self._notes.archive("现场记录", row["content_md"], draft_id)
        # 待办转任务草稿(方案 D):drafts 状态转换与 task_drafts 写入同一事务(repo 层),
        # 任一写入失败整体回滚——drafts 保持 pending 可重试,不留部分数据;
        # 文件已落盘而事务失败时,重试覆盖同一路径(不做文件版本系统)
        titles = (
            extract_todo_titles(row["content_md"]) if self._task_drafts is not None else None
        )
        if not self._drafts.confirm(draft_id, path, titles):
            # 并发或重复确认:不重复成功
            raise ValueError(draft_id)
        return path
