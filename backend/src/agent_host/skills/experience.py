"""经验沉淀 D1/D2:卡片草稿、入库、检索引用(FR-10;宪法第 8 条人工确认入库)。

原型期(Owner 决策)不接 LLM:D1 语音 → 模板化卡片草稿(与现场记录共用 drafts 表)。
"""

import re
from dataclasses import dataclass

from agent_host.store.repos import DraftRepo

_SENT_SPLIT = re.compile(r"[。!?;!?;\n]+")


@dataclass(frozen=True)
class ExperienceDraft:
    """经验卡片草稿(对应 drafts 表 kind='experience')。"""

    id: str
    title: str
    domain: str
    content_md: str
    status: str = "pending"  # pending / confirmed / discarded


def _render(record_id: str, transcript: str) -> tuple[str, str]:
    """标题取首句(≤30 字),正文为全文;领域原型期不自动分类。"""
    sentences = [s.strip() for s in _SENT_SPLIT.split(transcript) if s.strip()]
    title = (sentences[0][:30] if sentences else "(无有效转写内容)") or "(无有效转写内容)"
    content_md = "\n".join(
        [
            f"# {title}",
            "",
            "## 领域",
            "(待人工分类)",
            "",
            "## 内容",
            transcript,
            "",
        ]
    )
    return title, content_md


class ExperienceSkill:
    """process(record) / extract(doc)(08 §2)。"""

    def __init__(self, drafts: DraftRepo) -> None:
        self._drafts = drafts

    def process(self, record_id: str, transcript: str) -> ExperienceDraft:
        """D1:语音转写 → 经验卡片草稿,进入待确认队列(drafts kind='experience')。"""
        title, content_md = _render(record_id, transcript)
        draft_id = self._drafts.create(
            record_id=record_id, kind="experience", content_md=content_md
        )
        return ExperienceDraft(id=draft_id, title=title, domain="", content_md=content_md)

    def extract(self, doc_path: str) -> ExperienceDraft:
        """D2:已有文档 → 抽取经验卡片草稿。"""
        raise NotImplementedError
