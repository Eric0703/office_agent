"""意图路由:LLM 分类 + 规则兜底 + 显式模式优先(FR-04)。

auto 模式判定顺序:显式模式优先 → LLM 分类(注入真实 provider 时)→ 关键词规则兜底。
LLM 不可用(异常/非法输出)回退规则;LLM 置信度低于阈值 → unknown(反问,不再走规则);
mock provider 即"规则即 Mock"(app 装配传 llm=None)。unknown 不猜测执行(08 §1.2)。
"""

import logging
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_host.adapters.llm import LLMAdapter

logger = logging.getLogger(__name__)

# 指令参数类型最小校验表:值必须为 str 或 None(非法即视为非法 LLM 输出,回退规则)
_COMMAND_ENTITY_KEYS = {
    "complete_task": ("task_title",),
    "create_task": ("task_title", "due"),
    "create_reminder": ("remind_query",),
}

# 匹配前先剥掉标点/空白,降低 ASR 标点抖动的影响
_PUNCT_RE = re.compile(r"[,.!?;:,。,!?;:、·…—\s]+")

# ASR 同音字高频变体(benchmark §4:专名同音字是 small 的主要真实错误),先归一化再匹配
_ASR_VARIANTS = {"标记位": "标记为"}

# 白名单指令(FR-09,与 config 的 security.whitelist_commands 对齐)
# 完成指令只认显式命令式(标记/设为);"已完成/完成了"单独出现是陈述句,不作指令
# (L1 回归:FIELD-042"指标已经完成了百分之八十"误路由 task_command,零容忍方向,2026-07-22 收紧)
COMPLETE_CUES = ("标记为已完成", "标记完成", "标为完成", "设为完成")
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
# 一次性定时提醒(Owner 决策 2026-07-21:"定时任务"=可取消定时提醒,非周期 cron);
# 解析与确认在 skills.reminder,路由只产出指令名与原文;
# 不含裸"提醒"(陈述句"运维同学提醒"是告知不是指令,L1 回归 FIELD-012,2026-07-22 收紧)
REMIND_CUES = ("提醒我", "定时提醒", "定时任务")
# 口语并列待办弱线索(2026-07-21):并列词 + 动作动词才按多任务猜,且一律经预览确认;
# 常数放本模块,task_command 复用(避免循环导入)
TODO_VERBS = (
    "准备", "整理", "写", "发送", "提交", "回复", "完成", "处理", "买",
    "订", "联系", "安排", "打印", "修改", "做", "交", "发", "看", "学习", "弄",
)
MULTI_MARKERS = ("另外", "还有", "再就是", "还要", "也要")

# LLM 分类允许的指令白名单(与技能层能力对齐;白名单外由技能层显式拒绝,FR-09)
WHITELIST_COMMANDS = frozenset(
    {"complete_task", "create_task", "create_reminder", "list_today_tasks", "cancel_reminder"}
)

_LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["field_note", "task_command", "experience", "unknown"],
        },
        "confidence": {"type": "number"},
        "command": {"type": ["string", "null"]},
        "entities": {"type": "object"},
    },
    "required": ["intent", "confidence"],
}


def _build_llm_prompt(text: str) -> str:
    """意图分类提示词(四类 + 白名单指令;拿不准必须降置信度)。"""
    return (
        "你是语音工牌的意图分类器。把用户的一句话分到四类之一:\n"
        "- field_note(现场记录:陈述见闻、会议内容、情况描述,没有要求执行动作)\n"
        "- task_command(任务指令:要求执行动作,仅限指令 complete_task 完成任务 / "
        "create_task 新建任务 / create_reminder 新建定时提醒 / list_today_tasks 查询今日任务 / "
        "cancel_reminder 取消提醒)\n"
        "- experience(经验沉淀:复盘、教训、根因、以后应该怎么做)\n"
        "- unknown(无法理解、指代不明、或不像以上任何一类)\n"
        '只输出 JSON:{"intent": "...", "confidence": 0到1, "command": "指令名或null", '
        '"entities": {}}。\n'
        "entities 约定:complete_task → {\"task_title\": \"任务名\"};"
        "create_task → {\"task_title\": \"任务名\", \"due\": \"期限或null\"};"
        "create_reminder → {\"remind_query\": \"原文\"};其余 → {}。\n"
        "拿不准就把 confidence 降到 0.6 以下。只输出 JSON,不要解释。\n"
        f"用户文本:{text}"
    )

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
    """route(transcript, mode) → Intent(08 §2);显式模式优先,LLM 分类 + 规则兜底。"""

    def __init__(self, llm: LLMAdapter | None = None, min_confidence: float = 0.6) -> None:
        # llm=None:纯规则(mock provider 语义,规则即 Mock);min_confidence:LLM 反问阈值
        self._llm = llm
        self._min_confidence = min_confidence

    def route(self, transcript: str, mode: str = "auto") -> Intent:
        """显式模式(field/experience)直接采用;auto 先 LLM 分类,不可用则规则兜底。"""
        if mode == "field":
            return Intent(kind=IntentKind.FIELD_NOTE, confidence=1.0)
        if mode == "experience":
            return Intent(kind=IntentKind.EXPERIENCE, confidence=1.0)

        text = _normalize(transcript)
        if not text:
            return Intent(kind=IntentKind.UNKNOWN)

        if self._llm is not None:
            llm_intent = self._classify_llm(text)
            if llm_intent is not None:
                return llm_intent
            # LLM 不可用(异常/非法输出):回退规则兜底

        command = self._match_command(text)
        if command is not None:
            return command
        if any(cue in text for cue in EXPERIENCE_CUES):
            return Intent(kind=IntentKind.EXPERIENCE, confidence=0.8)
        # auto 模式下陈述性语音默认现场记录;空/非陈述已在上面排除
        return Intent(kind=IntentKind.FIELD_NOTE, confidence=0.5)

    def _classify_llm(self, text: str) -> Intent | None:
        """LLM 分类;返回值 None = LLM 不可用(异常/非法输出),调用方回退规则。

        置信度低于阈值 → unknown(反问,不再走规则);task_command 无指令名时
        尝试用规则补齐,补不出 → unknown(不猜测执行)。
        """
        try:
            raw = self._llm.complete(_build_llm_prompt(text), _LLM_SCHEMA)
        except Exception:
            logger.warning("LLM 分类调用失败,回退规则兜底", exc_info=True)
            return None
        try:
            kind = IntentKind(str(raw.get("intent")))
            confidence = raw.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("confidence 非数值")
            # 先比界(超大整数比较不抛异常),再查有限值;非法一律回退规则
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence 越界")
            if not math.isfinite(confidence):
                raise ValueError("confidence 非有限值")
            confidence = float(confidence)
            command = raw.get("command")
            if command is not None and command not in WHITELIST_COMMANDS:
                raise ValueError(f"白名单外指令:{command}")
            # entities:字段缺失可用空字典;字段存在必须是 dict([]/""/0/null 均非法)
            if "entities" in raw:
                entities = raw["entities"]
                if not isinstance(entities, dict):
                    raise ValueError("entities 非对象")
            else:
                entities = {}
            # 指令参数类型最小校验(str|None);类型非法 = 非法输出,回退规则
            for key in _COMMAND_ENTITY_KEYS.get(command, ()):
                if key in entities and not isinstance(entities[key], (str, type(None))):
                    raise ValueError(f"entities.{key} 类型非法")
        except (ValueError, TypeError):
            logger.warning("LLM 分类输出非法,回退规则兜底: %r", raw)
            return None
        if confidence < self._min_confidence:
            return Intent(kind=IntentKind.UNKNOWN, confidence=confidence)
        if kind is not IntentKind.TASK_COMMAND:
            return Intent(kind=kind, confidence=confidence)
        if command is None:
            filled = self._match_command(text)
            if filled is None:
                return Intent(kind=IntentKind.UNKNOWN, confidence=confidence)
            return Intent(kind=kind, confidence=confidence, command=filled.command,
                          entities=filled.entities)
        if command == "create_reminder":
            entities = {**entities, "remind_query": entities.get("remind_query") or text}
        return Intent(kind=kind, confidence=confidence, command=command, entities=entities)

    def _match_command(self, text: str) -> Intent | None:
        """白名单指令识别;识别到指令动词但抽不出参数时仍给指令名,由技能层处理。"""
        if any(cue in text for cue in COMPLETE_CUES):
            return Intent(
                kind=IntentKind.TASK_COMMAND,
                confidence=0.9,
                command="complete_task",
                entities={"task_title": _extract_complete_title(text)},
            )
        # 提醒类优先于新建:"创建一个定时任务"是定时提醒而非普通任务(Owner 决策)
        if "提醒" in text and "取消" in text:
            # 取消提醒不在本轮范围:明确拒绝,不得被提醒创建吞掉(不产生任何写入)
            return Intent(kind=IntentKind.TASK_COMMAND, confidence=0.9, command="cancel_reminder")
        if any(cue in text for cue in REMIND_CUES):
            return Intent(
                kind=IntentKind.TASK_COMMAND,
                confidence=0.9,
                command="create_reminder",
                entities={"remind_query": text},
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
        # 口语并列待办(无显式创建动词):并列词+动作动词才按多任务猜,
        # 一律经预览确认(needs_confirm);纯陈述不受影响,兜底仍走现场记录
        if any(m in text for m in MULTI_MARKERS) and any(v in text for v in TODO_VERBS):
            return Intent(
                kind=IntentKind.TASK_COMMAND,
                confidence=0.6,
                command="create_task",
                entities={"task_title": text, "needs_confirm": True},
            )
        return None
