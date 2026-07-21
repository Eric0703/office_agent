"""FastAPI app:HTTP/WS 端点装配、静态托管虚拟工牌(FR-12;登记册 §1.1 传输通道)。

本模块是装配根:把 adapters/store/router/skills/gateway 连成闭环(08 §2 依赖方向);
录音处理编排(08 §1.2)在此,业务规则仍归 router/skills。
原型期认证:dev_mode=auto_approve 时 hello 直通、音频上传只校验头存在(仅限原型)。
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_host.adapters.asr import FasterWhisperASR, MockASR
from agent_host.adapters.llm import create_llm_adapter
from agent_host.adapters.task import MockTaskAdapter
from agent_host.audio.pipeline import AudioPipeline
from agent_host.audit.logger import AuditEvent, AuditLogger
from agent_host.config import AppConfig, load_config
from agent_host.gateway.manager import ConnectionManager
from agent_host.router.router import Intent, IntentKind, IntentRouter
from agent_host.skills.experience import ExperienceSkill
from agent_host.skills.field_note import FieldNoteSkill
from agent_host.skills.task_command import ExecutionResult, TaskCommandSkill
from agent_host.store.db import init_db
from agent_host.store.repos import (
    AuditRepo,
    BriefingRepo,
    CardRepo,
    DeviceRepo,
    DraftRepo,
    RecordRepo,
    TaskRepo,
)

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 登记册 §2.2:上限 20MB,超限 413
FRONTEND_DIST = Path(__file__).resolve().parents[4] / "frontend" / "dist"
LOW_CONFIDENCE_THRESHOLD = 0.5  # 原型阈值(avg exp(logprob) 经验值),调优留后续任务卡


def create_app(config: AppConfig | None = None) -> FastAPI:
    """装配 HTTP/WS 端点与处理管线;frontend/dist 存在时静态托管,不存在则跳过。"""
    config = config or load_config("config.yaml")
    conn = init_db(config.store.db_path)
    devices = DeviceRepo(conn)
    records = RecordRepo(conn)
    cards = CardRepo(conn)
    drafts = DraftRepo(conn)
    audit = AuditLogger(AuditRepo(conn))

    asr = (
        MockASR()
        if config.asr.provider == "mock"
        else FasterWhisperASR(model_size=config.asr.model or "small")
    )
    llm = create_llm_adapter(config.llm)  # 默认 mock;真实 provider 双闸门,未配置显式报错(规约 §4)
    router = IntentRouter(llm)
    field_notes = FieldNoteSkill(drafts)
    experience = ExperienceSkill(drafts)
    task_commands = TaskCommandSkill(MockTaskAdapter(TaskRepo(conn)), cards)
    manager = ConnectionManager(
        devices, cards, BriefingRepo(conn), dev_mode=config.dev.dev_mode
    )
    audio = AudioPipeline(asr, config.audio.tmp_dir, records, config.audio.delete_after_transcribe)

    app = FastAPI(title="agent-host", version="0.2.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def control_channel(websocket: WebSocket) -> None:
        await manager.handle_connection(websocket)

    @app.post("/audio/{record_id}")
    async def upload_audio(record_id: str, request: Request) -> JSONResponse:
        """音频受理:登记 records 后后台异步处理,结果经 WS 回 intent.result。"""
        # 原型期 dev_mode:只校验头存在,token 校验留待正式配对照(后续任务卡)
        device_id = request.headers.get("x-device-id", "")
        if not device_id:
            return JSONResponse(status_code=401, content={"detail": "missing X-Device-Id"})
        body = await request.body()
        if len(body) > MAX_AUDIO_BYTES:
            return JSONResponse(status_code=413, content={"detail": "audio too large"})
        if records.get(record_id) is not None:
            # 幂等:同 record_id 重传返回首次受理结果(登记册 §2.2)
            return JSONResponse({"status": "duplicate", "record_id": record_id})
        audio.save_upload(
            record_id=record_id,
            device_id=device_id,
            mode=manager.pop_record_mode(record_id) or "auto",
            started_at=datetime.now(UTC).isoformat(),
            duration_ms=int(request.headers.get("x-duration-ms", "0") or 0),
            data=body,
            fmt=request.headers.get("x-audio-format", "webm-opus"),
        )
        asyncio.create_task(_process_record(record_id))
        return JSONResponse({"status": "received", "record_id": record_id})

    async def _process_record(record_id: str) -> None:
        """转写 → 清理 → 路由 → 执行/草稿 → 回送(08 §1.2 主机侧管线)。"""
        row = records.get(record_id)
        if row is None:
            return
        device_id: str = row["device_id"]
        try:
            text, confidence = await asyncio.to_thread(
                audio.transcribe_file, row["audio_tmp_path"]
            )
        except Exception:
            logger.exception("ASR 转写失败 record_id=%s", record_id)
            audio.cleanup(record_id)  # 失败同样删除原始音频(宪法第 3 条)
            records.update_status(record_id, "failed")
            await manager.push(
                device_id,
                "intent.result",
                {
                    "record_id": record_id,
                    "status": "failed",
                    "title": "转写失败,请重说",
                    "error_code": "ASR_FAILED",
                },
            )
            return
        audio.cleanup(record_id)  # 转写成功后立即删除原始音频(宪法第 3 条)
        records.set_transcript(record_id, text, confidence)
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            records.update_status(record_id, "failed")
            await manager.push(
                device_id,
                "intent.result",
                {
                    "record_id": record_id,
                    "status": "low_confidence",
                    "title": "没听清,请到安静处重说",
                    "error_code": "ASR_LOW_CONFIDENCE",
                },
            )
            return

        intent = router.route(text, mode=row["mode"])
        records.set_intent(record_id, intent.command or intent.kind.value)
        records.update_status(record_id, "routed")
        if intent.kind is IntentKind.TASK_COMMAND:
            await _run_task_command(device_id, record_id, intent)
        elif intent.kind in (IntentKind.FIELD_NOTE, IntentKind.EXPERIENCE):
            if intent.kind is IntentKind.FIELD_NOTE:
                field_notes.process(record_id, text)
                title = "笔记草稿已生成"
            else:
                experience.process(record_id, text)
                title = "经验卡片草稿已生成"
            records.update_status(record_id, "done")
            await manager.push(
                device_id,
                "intent.result",
                {
                    "record_id": record_id,
                    "status": "success",
                    "title": title,
                    "body": "请到 PC 确认归档(宪法第 8 条)",
                },
            )
            audit.log(
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
        else:  # unknown:不猜测执行(08 §1.2)
            records.update_status(record_id, "failed")
            await manager.push(
                device_id,
                "intent.result",
                {
                    "record_id": record_id,
                    "status": "failed",
                    "title": "没有理解,请换种说法",
                    "error_code": "INTENT_UNKNOWN",
                },
            )
            audit.log(
                AuditEvent(
                    device_id=device_id,
                    decision="failed",
                    record_id=record_id,
                    intent="unknown",
                    result="INTENT_UNKNOWN",
                )
            )

    async def _run_task_command(device_id: str, record_id: str, intent: Intent) -> None:
        result = task_commands.execute(intent, record_id)
        await _push_execution_result(device_id, result)
        if result.status == "clarify":
            return  # 中间态:等 clarify.select,终态再审计
        records.update_status(record_id, "done" if result.status == "success" else "failed")
        audit.log(
            AuditEvent(
                device_id=device_id,
                decision="executed" if result.status == "success" else "failed",
                record_id=record_id,
                intent=intent.command,
                risk_level="L1" if intent.command == "complete_task" else "L0",
                tool=result.tool,
                result=result.title,
            )
        )

    async def _push_execution_result(device_id: str, result: ExecutionResult) -> None:
        payload: dict[str, object] = {
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
        await manager.push(device_id, "intent.result", payload)
        for card_id in result.dismissed_card_ids:
            # "说完即消"(08 §1.3):语音完成任务 → 撤下对应卡片
            await manager.push(
                device_id, "reminder.dismiss", {"card_id": card_id, "reason": "completed"}
            )

    async def _on_clarify(device_id: str, record_id: str, candidate_id: str) -> None:
        """clarify.select:candidate_id 即 task_id,执行并回终态(登记册 §2.3)。"""
        try:
            result = task_commands.complete_by_id(candidate_id, record_id)
        except KeyError:
            return
        await _push_execution_result(device_id, result)
        records.update_status(record_id, "done")
        audit.log(
            AuditEvent(
                device_id=device_id,
                decision="executed",
                record_id=record_id,
                intent="complete_task",
                risk_level="L1",
                tool="complete_task",
                result=result.title,
            )
        )

    manager.clarify_handler = _on_clarify

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="vbadge")

    return app
