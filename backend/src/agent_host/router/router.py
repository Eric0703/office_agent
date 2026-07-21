"""意图路由:LLM 分类 + 规则兜底 + 显式模式优先(FR-04)。

原型期(Owner 决策)不接真实 LLM:auto 模式用关键词规则,规则即"Mock LLM";
unknown 不猜测执行(08 §1.2)。只经 adapters.llm 调模型的约束在接真实模型时生效。
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_host.adapters.llm import LLMAdapter

# 匹配前先剥掉标点/空白,降低 ASR 标点抖动的影响
_PUNCT_RE = re.compile(r"[,.!?;:,。,!?;:、·…—\s]+")

# ASR 同音字高频变体(benchmark §4:专名同音字是 small 的主要真实错误),先归一化再匹配
_ASR_VARIANTS = {"标记位": "标记为"}

# 白名单指令(FR-09,与 config 的 security.whitelist_commands 对齐)
COMPLETE_CUES = ("标记为已完成", "标记完成", "标为完成", "设为完成", "已完成", "完成了", "做完了")
CREATE_CUES = (
    "新建一个任务", "新建任务", "创建一个任务", "创建任务", "添加任务", "新建", "创建", "添加",
)
# 只读查询(L0)的保守 ASR 变体(Gate 0 反馈:「查一下」常被转写为「插一下/茶一下」);
# 变体仅用于只读查询,写操作指令不增设变体,避免扩大误触范围(Owner 指令)
LIST_CUES = (
    "查一下", "查一查", "查下", "插一下", "插一查", "插下", "茶一下",
    "哪些任务", "还有什么任务", "今天的任务", "今日任务", "待办",
)
EXPERIENCE_CUES = ("教训", "根因", "复盘", "经验", "以后任何", "以后要", "切记")

_DUE_RE = re.compile(r"^(明天|今天|后天|下周\S{0,2}?)(?:之前|以前|前)(?P<rest>.+)$")


class IntentKind(StrEnum):
    """三类意图 + unknown(08 §1.2:unknown 不猜测执行)。"""

    FIELD_NOTE = "field_note"
    TASK_COMMAND = "task_command"
    EXPERIENCE = "experience"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Intent:
    """路由结果;command 为白名单指令名,由 security 层校验(FR-09)。"""

    kind: IntentKind
    confidence: float = 0.0
    command: str | None = None
    entities: dict[str, Any] = field(default_factory=dict)


def _normalize(text: str) -> str:
    text = _PUNCT_RE.sub("", text)
    for variant, canonical in _ASR_VARIANTS.items():
        text = text.replace(variant, canonical)
    return text


def _extract_complete_title(text: str) -> str | None:
    """"把 X 标记为已完成" → X;取指令提示词之前的片段,去掉引导的"把"。"""
    for cue in COMPLETE_CUES:  # 已按长→短排列,优先吃最长匹配
        if cue in text:
            head = text.split(cue, 1)[0].lstrip("把").strip()
            return head or None
    return None


def _extract_create(text: str) -> dict[str, str | None]:
    """"新建一个任务,明天之前回复客户邮件" → {task_title, due}(原型只认常见期限词)。"""
    for cue in CREATE_CUES:
        if cue not in text:
            continue
        tail = text.split(cue, 1)[1].strip()
        if not tail:
            break
        m = _DUE_RE.match(tail)
        if m:
            return {"task_title": m.group("rest"), "due": m.group(1)}
        return {"task_title": tail, "due": None}
    return {"task_title": None, "due": None}


class IntentRouter:
    """route(transcript, mode) → Intent(08 §2);显式模式优先,auto 走关键词规则。"""

    def __init__(self, llm: LLMAdapter | None = None) -> None:
        # 原型期不接真实 LLM;保留注入点,接真实模型时由 adapters.llm 替换规则
        self._llm = llm

    def route(self, transcript: str, mode: str = "auto") -> Intent:
        """显式模式(field/experience)直接采用;auto 按 指令→经验→现场记录 判定。"""
        if mode == "field":
            return Intent(kind=IntentKind.FIELD_NOTE, confidence=1.0)
        if mode == "experience":
            return Intent(kind=IntentKind.EXPERIENCE, confidence=1.0)

        text = _normalize(transcript)
        if not text:
            return Intent(kind=IntentKind.UNKNOWN)

        command = self._match_command(text)
        if command is not None:
            return command
        if any(cue in text for cue in EXPERIENCE_CUES):
            return Intent(kind=IntentKind.EXPERIENCE, confidence=0.8)
        # auto 模式下陈述性语音默认现场记录;空/非陈述已在上面排除
        return Intent(kind=IntentKind.FIELD_NOTE, confidence=0.5)

    def _match_command(self, text: str) -> Intent | None:
        """白名单指令识别;识别到指令动词但抽不出参数时仍给指令名,由技能层处理。"""
        if any(cue in text for cue in COMPLETE_CUES):
            return Intent(
                kind=IntentKind.TASK_COMMAND,
                confidence=0.9,
                command="complete_task",
                entities={"task_title": _extract_complete_title(text)},
            )
        if any(cue in text for cue in CREATE_CUES):
            entities = _extract_create(text)
            return Intent(
                kind=IntentKind.TASK_COMMAND,
                confidence=0.9,
                command="create_task",
                entities=entities,
            )
        if any(cue in text for cue in LIST_CUES):
            return Intent(
                kind=IntentKind.TASK_COMMAND, confidence=0.9, command="list_today_tasks"
            )
        return None
