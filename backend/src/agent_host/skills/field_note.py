"""现场记录:结构化笔记草稿、待确认队列、归档(FR-05)。

宪法第 8 条:人工确认前产出物一律是草稿;草稿确认后才经 NotesAdapter 归档。
原型期(Owner 决策)不接 LLM:模板 + 抽取式填充;草稿正文面向用户,不含内部术语。
"""

import re
from dataclasses import dataclass

from agent_host.store.repos import DraftRepo

_SENT_SPLIT = re.compile(r"[。!?;!?;\n]+")
_CONCLUSION_CUES = ("所以", "因此", "决定", "达成一致", "结论")
_TODO_CUES = ("需要", "待办", "下一步", "尽快", "务必", "记得")


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

    def __init__(self, drafts: DraftRepo) -> None:
        self._drafts = drafts

    def process(self, record_id: str, transcript: str) -> NoteDraft:
        """转写文本 → 结构化笔记草稿,写入 drafts 表(pending);音频由 audio 管线即删。"""
        content_md = _render(record_id, transcript)
        draft_id = self._drafts.create(record_id=record_id, kind="note", content_md=content_md)
        return NoteDraft(id=draft_id, record_id=record_id, content_md=content_md)

    def archive(self, draft_id: str) -> str:
        """人工确认后归档为 Markdown,返回文件路径。"""
        raise NotImplementedError
