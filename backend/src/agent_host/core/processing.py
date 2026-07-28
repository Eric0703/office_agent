"""文本处理核心编排(08 §1.2 主机侧管线的核心段):置信度闸门 → 路由 → 执行/草稿 → 出站。

本模块为核心层:不依赖任何 Web 框架,全部外部依赖(repos/router/skills/audit/
阈值配置)经 ProcessingDeps 注入,由装配根 api/app.py 构建;
core 不做任何推送:结果以 ProcessOutcome 返回,投递目标由外层适配器决定
(设备音频路径投递到 records 行的 device_id;PC 文字等入口由各自适配器决定);
音频取行/转写/清理同样归装配根编排,core 只处理文本(设备录音/PC 录音/音频文件/PC 文字复用)。
来源中立:record_id 缺省时按 source + 可空 device_id 真实登记 records 行,
不伪造设备、不需要预建录音记录;audit 的 device_id 允许落 NULL。
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_host.audit.logger import AuditEvent, AuditLogger
from agent_host.router.router import Intent, IntentKind, IntentRouter
from agent_host.skills.experience import ExperienceSkill
from agent_host.skills.field_note import FieldNoteSkill
from agent_host.skills.reminder import ReminderSkill
from agent_host.skills.task_command import ExecutionResult, TaskCommandSkill
from agent_host.store.repos import RecordRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundMessage:
    """一条待投递的控制通道消息(msg_type + 业务 payload;信封封装在 gateway._send)。"""

    msg_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProcessOutcome:
    """一次处理的出站结果:record_id + 有序消息序列(投递目标由外层适配器决定)。

    顺序契约与现行推送顺序一致:intent.result 在前,逐卡 reminder.dismiss 在后。
    落库/缓存/审计在 core 返回 outcome 之前已全部完成,投递失败不可能再中断它们。
    """

    record_id: str
    messages: list[OutboundMessage] = field(default_factory=list)


@dataclass
class ProcessingDeps:
    """文本处理编排的全部外部依赖(装配根注入;core 内不构造任何具体组件)。"""

    records: RecordRepo
    router: IntentRouter
    field_notes: FieldNoteSkill
    experience: ExperienceSkill
    reminders: ReminderSkill
    task_commands: TaskCommandSkill
    audit: AuditLogger
    low_confidence_threshold: float  # ASR 置信度闸门(config.asr.low_confidence_threshold)
    cache_result: Callable[[dict[str, Any]], None]  # intent.result 缓存(duplicate 补推,A1-2)


def _intent_result_message(deps: ProcessingDeps, payload: dict[str, Any]) -> OutboundMessage:
    """intent.result 统一出口:先缓存后入队;进程内重启丢失,重连仍走 state.sync。"""
    deps.cache_result(payload)
    return OutboundMessage("intent.result", payload)


def _execution_messages(deps: ProcessingDeps, result: ExecutionResult) -> list[OutboundMessage]:
    """执行结果 → 出站序列:intent.result(先缓存)在前,逐卡 reminder.dismiss 在后(08 §1.3)。"""
    payload: dict[str, Any] = {
        "record_id": result.record_id,
        "status": result.status,
        "title": result.title,
    }
    if result.body:
        payload["body"] = result.body
    if result.candidates:
        payload["candidates"] = list(result.candidates)
    if result.error_code:
        payload["error_code"] = result.error_code
    messages = [_intent_result_message(deps, payload)]
    for card_id in result.dismissed_card_ids:
        # "说完即消"(08 §1.3):语音完成任务 → 撤下对应卡片
        messages.append(
            OutboundMessage("reminder.dismiss", {"card_id": card_id, "reason": "completed"})
        )
    return messages


def process_text(
    deps: ProcessingDeps,
    *,
    text: str,
    confidence: float,
    mode: str = "auto",
    source: str = "pc_text",
    record_id: str | None = None,
    device_id: str | None = None,
) -> ProcessOutcome:
    """来源中立文本处理入口:置信度闸门 → 路由 → 分发 → 执行/草稿 → 终态 → 缓存 → 审计。

    设备录音路径传入既有 record_id(装配根已登记 records 行),行为不变;
    record_id 缺省(PC 文字/PC 录音/音频文件)时按 source + 可空 device_id 真实登记,
    不伪造设备、不需要预建录音记录。不做任何推送;终态/缓存/审计先于返回完成。
    """
    if record_id is None:
        record_id = f"rec-{uuid.uuid4().hex[:12]}"
        deps.records.create(
            record_id=record_id,
            device_id=device_id,
            mode=mode,
            started_at=datetime.now(UTC).isoformat(),
            duration_ms=0,
            source=source,
        )
        deps.records.set_transcript(record_id, text, confidence)
    if confidence < deps.low_confidence_threshold:
        deps.records.update_status(record_id, "failed")
        message = _intent_result_message(
            deps,
            {
                "record_id": record_id,
                "status": "low_confidence",
                "title": "没有听清,请重新录音",
                "error_code": "ASR_LOW_CONFIDENCE",
            },
        )
        return ProcessOutcome(record_id, [message])

    intent = deps.router.route(text, mode=mode)
    deps.records.set_intent(record_id, intent.command or intent.kind.value)
    deps.records.update_status(record_id, "routed")
    if intent.kind is IntentKind.TASK_COMMAND:
        return _run_task_command(deps, device_id, record_id, intent)
    if intent.kind in (IntentKind.FIELD_NOTE, IntentKind.EXPERIENCE):
        if intent.kind is IntentKind.FIELD_NOTE:
            deps.field_notes.process(record_id, text)
            title = "笔记草稿已生成"
        else:
            deps.experience.process(record_id, text)
            title = "经验卡片草稿已生成"
        deps.records.update_status(record_id, "done")
        message = _intent_result_message(
            deps,
            {
                "record_id": record_id,
                "status": "success",
                "title": title,
                "body": "请到电脑端查看待确认草稿",
            },
        )
        deps.audit.log(
            AuditEvent(
                device_id=device_id,
                decision="executed",
                record_id=record_id,
                intent=intent.kind.value,
                risk_level="L0",
                tool="draft",
                result=title,
            )
        )
        return ProcessOutcome(record_id, [message])
    # unknown:不猜测执行(08 §1.2)
    deps.records.update_status(record_id, "failed")
    message = _intent_result_message(
        deps,
        {
            "record_id": record_id,
            "status": "failed",
            "title": "没有理解,请换种说法",
            "error_code": "INTENT_UNKNOWN",
        },
    )
    deps.audit.log(
        AuditEvent(
            device_id=device_id,
            decision="failed",
            record_id=record_id,
            intent="unknown",
            result="INTENT_UNKNOWN",
        )
    )
    return ProcessOutcome(record_id, [message])


def _run_task_command(
    deps: ProcessingDeps, device_id: str | None, record_id: str, intent: Intent
) -> ProcessOutcome:
    if intent.command == "create_reminder":
        # 一次性定时提醒:解析明确才直建,不确定经 clarify 确认,确认前不写入(FR-07)
        result = deps.reminders.execute(intent.entities.get("remind_query", ""), record_id)
    else:
        result = deps.task_commands.execute(intent, record_id)
    messages = _execution_messages(deps, result)
    if result.status == "clarify":
        return ProcessOutcome(record_id, messages)  # 中间态:等 clarify.select,终态再审计
    deps.records.update_status(record_id, "done" if result.status == "success" else "failed")
    deps.audit.log(
        AuditEvent(
            device_id=device_id,
            decision="executed" if result.status == "success" else "failed",
            record_id=record_id,
            intent=intent.command,
            risk_level="L1" if intent.command in ("complete_task", "create_reminder") else "L0",
            tool=result.tool,
            result=result.title,
        )
    )
    return ProcessOutcome(record_id, messages)


def record_asr_failure(deps: ProcessingDeps, *, record_id: str) -> ProcessOutcome:
    """ASR 失败分支:status=failed + 缓存(现无审计,保持无审计);音频清理归装配根。"""
    deps.records.update_status(record_id, "failed")
    message = _intent_result_message(
        deps,
        {
            "record_id": record_id,
            "status": "failed",
            "title": "未能识别,请在安静处重新录音",
            "error_code": "ASR_FAILED",
        },
    )
    return ProcessOutcome(record_id, [message])


def on_clarify(
    deps: ProcessingDeps,
    *,
    device_id: str | None,
    record_id: str,
    candidate_id: str,
    edited_labels: list[str] | None = None,
) -> ProcessOutcome | None:
    """clarify.select:任务候选(task_id)/提醒确认(remind:*)/多任务预览(task:*),回终态。

    task:cancel / remind:cancel = 取消终态:不执行任何候选,records done,
    audit decision='cancelled',终态 intent.result("已取消")入缓存——
    离线凭据可出队,duplicate 恢复重放终态,不会回到候选页。
    未知候选(complete_by_id KeyError)返回 None,保持现行"直接 return"语义(无出站消息)。
    """
    is_remind = candidate_id.startswith("remind:")
    is_task = candidate_id.startswith("task:")
    if candidate_id == "task:cancel":
        result = deps.task_commands.cancel_pending(record_id)
    elif is_remind:
        result = deps.reminders.confirm_pending(record_id, candidate_id)
    elif is_task:
        result = deps.task_commands.confirm_create(record_id, candidate_id, edited_labels)
    else:
        try:
            result = deps.task_commands.complete_by_id(candidate_id, record_id)
        except KeyError:
            return None
    messages = _execution_messages(deps, result)
    deps.records.update_status(record_id, "done")
    if candidate_id in ("remind:cancel", "task:cancel"):
        decision = "cancelled"
    elif is_remind or is_task:
        decision = "confirmed"
    else:
        decision = "executed"
    if is_remind:
        intent_label = "create_reminder"
    elif candidate_id == "task:cancel":
        # 取消的意图标签取 records 已登记的路由意图(歧义 complete_task / 预览 create_task)
        row = deps.records.get(record_id)
        intent_label = row["intent"] if row is not None and row["intent"] else "create_task"
    elif is_task:
        intent_label = "create_task"
    else:
        intent_label = "complete_task"
    deps.audit.log(
        AuditEvent(
            device_id=device_id,
            decision=decision,
            record_id=record_id,
            intent=intent_label,
            risk_level="L1",
            tool=result.tool,
            result=result.title,
        )
    )
    return ProcessOutcome(record_id, messages)
