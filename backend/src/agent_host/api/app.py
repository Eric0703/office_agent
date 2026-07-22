"""FastAPI app:HTTP/WS 端点装配、静态托管虚拟工牌(FR-12;登记册 §1.1 传输通道)。

本模块是装配根:把 adapters/store/router/skills/gateway 连成闭环(08 §2 依赖方向);
录音处理编排(08 §1.2)在此,业务规则仍归 router/skills。
原型期认证:dev_mode=auto_approve 时 hello 直通、音频上传只校验头存在(仅限原型)。
"""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Row

from fastapi import BackgroundTasks, FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_host.adapters.asr import ASRAdapter, FasterWhisperASR, MockASR
from agent_host.adapters.llm import create_llm_adapter
from agent_host.adapters.task import MockTaskAdapter
from agent_host.audio.pipeline import AudioPipeline
from agent_host.audit.logger import AuditEvent, AuditLogger
from agent_host.config import AppConfig, load_config
from agent_host.gateway.manager import ConnectionManager
from agent_host.router.router import Intent, IntentKind, IntentRouter
from agent_host.scheduler.jobs import reminder_loop
from agent_host.skills.experience import ExperienceSkill
from agent_host.skills.field_note import FieldNoteSkill
from agent_host.skills.reminder import ReminderSkill
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


def _desk_status(row: Row) -> str:
    """records 行 → 工作台用户可读状态;error_code 等内部细节不出现在返回值。"""
    status = row["status"]
    if status in ("uploaded", "transcribed", "routed"):
        return "处理中"
    if status == "done":
        return "已生成草稿" if row["intent"] in ("field_note", "experience") else "指令已执行"
    if row["transcript"] is None:
        return "转写失败"
    if row["intent"] == "unknown":
        return "未理解"
    return "未听清"


_LEGACY_DRAFT_MARKERS = ("- 来源 record_id:", "- 生成方式:")


def _synthesize_result(row: Row) -> dict[str, object]:
    """records 终态行 → 通用 intent.result(结果缓存淘汰或服务重启后的降级恢复:
    精确标题不可重建时,给出不撒谎的通用终态,详情引导到电脑端查看)。"""
    if row["status"] == "done":
        return {
            "record_id": row["id"],
            "status": "success",
            "title": "已处理完成",
            "body": "请到电脑端查看详情",
        }
    return {
        "record_id": row["id"],
        "status": "failed",
        "title": "处理未成功,请重新录音",
        "error_code": "INTERNAL",
    }


def _desk_draft_content(content_md: str) -> str:
    """剔除历史草稿(修复前生成)里的内部标注行;仅展示层处理,数据库原文不动。"""
    kept = [
        line for line in content_md.splitlines() if not line.startswith(_LEGACY_DRAFT_MARKERS)
    ]
    return "\n".join(kept).replace("(Mock 草稿)", "")


def create_app(config: AppConfig | None = None, asr: ASRAdapter | None = None) -> FastAPI:
    """装配 HTTP/WS 端点与处理管线;frontend/dist 存在时静态托管,不存在则跳过。

    asr 可注入(测试以 MockASR 固定文本走全管线);缺省按 config 装配,
    faster-whisper 经 hotwords 传入本机业务词表(仅改善识别,不改变路由/确认语义)。
    """
    config = config or load_config("config.yaml")
    conn = init_db(config.store.db_path)
    devices = DeviceRepo(conn)
    records = RecordRepo(conn)
    cards = CardRepo(conn)
    drafts = DraftRepo(conn)
    tasks_repo = TaskRepo(conn)
    audit = AuditLogger(AuditRepo(conn))

    if asr is None:
        asr = (
            MockASR()
            if config.asr.provider == "mock"
            else FasterWhisperASR(
                model_size=config.asr.model or "small", hotwords=config.asr.hotwords
            )
        )
    llm = create_llm_adapter(config.llm)  # 默认 mock;真实 provider 双闸门,未配置显式报错(规约 §4)
    router = IntentRouter(llm)
    field_notes = FieldNoteSkill(drafts)
    experience = ExperienceSkill(drafts)
    reminders = ReminderSkill(cards, tasks_repo)
    task_commands = TaskCommandSkill(MockTaskAdapter(tasks_repo), cards)
    manager = ConnectionManager(
        devices, cards, BriefingRepo(conn), dev_mode=config.dev.dev_mode
    )
    audio = AudioPipeline(asr, config.audio.tmp_dir, records, config.audio.delete_after_transcribe)

    result_cache: dict[str, dict[str, object]] = {}  # record_id → intent.result 负载(内存,≤200 条)

    def cache_result(payload: dict[str, object]) -> None:
        """缓存 intent.result,供 duplicate 补推(A1-2:已受理但响应/推送在断线中丢失的恢复)。"""
        record_id = str(payload.get("record_id", ""))
        if not record_id:
            return
        if len(result_cache) >= 200:
            result_cache.pop(next(iter(result_cache)))
        result_cache[record_id] = payload

    async def push_result(device_id: str, payload: dict[str, object]) -> None:
        """intent.result 统一出口:先缓存后推送;进程内重启丢失,重连仍走 state.sync。"""
        cache_result(payload)
        await manager.push(device_id, "intent.result", payload)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 提醒最小到点触发(08 §2;触发记录仅内存,重启后过期未撤卡会补触发一次)
        scheduler_task = asyncio.create_task(reminder_loop(cards, manager.broadcast))
        yield
        scheduler_task.cancel()

    app = FastAPI(title="agent-host", version="0.2.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/desk/records")
    async def desk_records() -> list[dict[str, object]]:
        """PC 草稿工作台:最近 20 条处理记录(只读,本机演示;不含内部编号/错误码)。"""
        return [
            {
                "created_at": row["created_at"],
                "status": _desk_status(row),
                "transcript": row["transcript"],
                "confidence": row["confidence"],
            }
            for row in records.list_recent(20)
        ]

    @app.get("/desk/drafts")
    async def desk_drafts() -> list[dict[str, object]]:
        """PC 草稿工作台:待确认草稿队列(只读;归档确认未实现,不提供写接口)。

        历史草稿经 _desk_draft_content 剔除内部标注行后返回,数据库原文不动。
        """
        return [
            {
                "kind": row["kind"],
                "created_at": row["created_at"],
                "content_md": _desk_draft_content(row["content_md"]),
                "status": row["status"],
            }
            for row in drafts.list_pending()
        ]

    @app.get("/desk/tasks")
    async def desk_tasks() -> list[dict[str, object]]:
        """PC 工作台:待办任务 + 定时提醒(只读;新建任务/提醒创建的可观察证据)。"""
        items: list[dict[str, object]] = [
            {
                "id": row["id"],
                "kind": "task",
                "title": row["title"],
                "time": row["due_at"],
                "status": "已完成" if row["status"] == "done" else "未完成",
                "created_at": row["created_at"],
            }
            for row in tasks_repo.list_all(50)
        ]
        items += [
            {
                "id": row["id"],
                "kind": "timer",
                "title": row["title"],
                "time": row["remind_at"],
                "status": "生效中" if row["status"] == "active" else "已撤下",
                "created_at": row["created_at"],
            }
            for row in cards.list_by_kind("timer", 50)
        ]
        items.sort(key=lambda item: str(item["created_at"]), reverse=True)
        return items[:50]

    @app.post("/desk/tasks/{task_id}/complete")
    async def desk_complete_task(task_id: str) -> JSONResponse:
        """PWA/工作台勾选完成任务(FR-07 闭环):写 tasks.done、撤对应卡并广播 reminder.dismiss。"""
        try:
            result = task_commands.complete_by_id(task_id, f"pc-{task_id[:8]}")
        except KeyError:
            return JSONResponse(status_code=404, content={"detail": "task not found"})
        for card_id in result.dismissed_card_ids:
            await manager.broadcast("reminder.dismiss", {"card_id": card_id, "reason": "completed"})
        audit.log(
            AuditEvent(
                device_id="pc-desk",
                decision="executed",
                intent="complete_task",
                risk_level="L1",
                tool="complete_task",
                result=result.title,
            )
        )
        return JSONResponse({"status": "ok", "title": result.title})

    @app.post("/desk/reminders/{card_id}/cancel")
    async def desk_cancel_reminder(card_id: str) -> JSONResponse:
        """取消一次性定时提醒(触控/PC 路径):撤下 timer 卡并广播;语音取消本轮仍拒绝。"""
        card = cards.get(card_id)
        if card is None or card["status"] != "active":
            return JSONResponse(status_code=404, content={"detail": "reminder not found"})
        cards.dismiss(card_id, "cancelled")
        await manager.broadcast("reminder.dismiss", {"card_id": card_id, "reason": "cancelled"})
        audit.log(
            AuditEvent(
                device_id="pc-desk",
                decision="cancelled",
                intent="cancel_reminder",
                risk_level="L1",
                tool="cancel_reminder",
                result=card["title"],
            )
        )
        return JSONResponse({"status": "ok"})

    @app.post("/desk/pair/approve")
    async def desk_pair_approve(request: Request) -> JSONResponse:
        """Owner 批准配对码(本机接口;CLI `agent-host pair approve` 经此触发,登记册 §2.1)。"""
        body = await request.json()
        device_id = await manager.approve_pair(str(body.get("code", "")))
        if device_id is None:
            return JSONResponse(
                status_code=404, content={"detail": "pair code not found or expired"}
            )
        return JSONResponse({"status": "approved", "device_id": device_id})

    @app.post("/desk/pair/revoke")
    async def desk_pair_revoke(request: Request) -> JSONResponse:
        """吊销设备(本机接口):token 立即失效并向在线设备推送 device.revoke。"""
        body = await request.json()
        ok = await manager.revoke(str(body.get("device_id", "")))
        if not ok:
            return JSONResponse(
                status_code=404, content={"detail": "device not found or already revoked"}
            )
        return JSONResponse({"status": "revoked"})

    @app.websocket("/ws")
    async def control_channel(websocket: WebSocket) -> None:
        await manager.handle_connection(websocket)

    @app.post("/audio/{record_id}")
    async def upload_audio(
        record_id: str, request: Request, background: BackgroundTasks
    ) -> JSONResponse:
        """音频受理:登记 records 后后台异步处理,结果经 WS 回 intent.result。

        用 Starlette BackgroundTasks 而非裸 create_task:响应先回送,任务在
        ASGI 周期内受控执行(TestClient 下可测,行为与 uvicorn 一致)。
        """
        # dev_mode=auto_approve 时只校验头存在(原型旁路);正式路径校验 token 与吊销态(A1)
        device_id = request.headers.get("x-device-id", "")
        if not device_id:
            return JSONResponse(status_code=401, content={"detail": "missing X-Device-Id"})
        if config.dev.dev_mode != "auto_approve":
            device = devices.get(device_id)
            token_hash = hashlib.sha256(request.headers.get("x-token", "").encode()).hexdigest()
            if device is None or device["revoked_at"] or device["token_hash"] != token_hash:
                return JSONResponse(status_code=401, content={"detail": "invalid device token"})
        body = await request.body()
        if len(body) > MAX_AUDIO_BYTES:
            return JSONResponse(status_code=413, content={"detail": "audio too large"})
        existing = records.get(record_id)
        if existing is not None:
            # 幂等:同 record_id 重传返回首次受理结果(登记册 §2.2);
            # 归属校验(A1-2):仅向 records 所属设备补推,其他配对设备不得跨设备读取结果;
            # 已有终态:缓存补推;缓存淘汰/重启:按 records 终态合成通用结果;
            # 处理中:不推(原任务终态推送)
            if existing["device_id"] == device_id:
                cached = result_cache.get(record_id)
                if cached is not None:
                    await manager.push(device_id, "intent.result", cached)
                elif existing["status"] in ("done", "failed"):
                    await manager.push(device_id, "intent.result", _synthesize_result(existing))
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
        background.add_task(_process_record, record_id)
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
            await push_result(
                device_id,
                {
                    "record_id": record_id,
                    "status": "failed",
                    "title": "未能识别,请在安静处重新录音",
                    "error_code": "ASR_FAILED",
                },
            )
            return
        audio.cleanup(record_id)  # 转写成功后立即删除原始音频(宪法第 3 条)
        records.set_transcript(record_id, text, confidence)
        if confidence < config.asr.low_confidence_threshold:
            records.update_status(record_id, "failed")
            await push_result(
                device_id,
                {
                    "record_id": record_id,
                    "status": "low_confidence",
                    "title": "没有听清,请重新录音",
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
            await push_result(
                device_id,
                {
                    "record_id": record_id,
                    "status": "success",
                    "title": title,
                    "body": "请到电脑端查看待确认草稿",
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
            await push_result(
                device_id,
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
        if intent.command == "create_reminder":
            # 一次性定时提醒:解析明确才直建,不确定经 clarify 确认,确认前不写入(FR-07)
            result = reminders.execute(intent.entities.get("remind_query", ""), record_id)
        else:
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
                risk_level="L1" if intent.command in ("complete_task", "create_reminder") else "L0",
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
        await push_result(device_id, payload)
        for card_id in result.dismissed_card_ids:
            # "说完即消"(08 §1.3):语音完成任务 → 撤下对应卡片
            await manager.push(
                device_id, "reminder.dismiss", {"card_id": card_id, "reason": "completed"}
            )

    async def _on_clarify(
        device_id: str, record_id: str, candidate_id: str, edited_labels: list[str] | None = None
    ) -> None:
        """clarify.select:任务候选(task_id)/提醒确认(remind:*)/多任务预览(task:*),回终态。"""
        is_remind = candidate_id.startswith("remind:")
        is_task_preview = candidate_id.startswith("task:")
        if is_remind:
            result = reminders.confirm_pending(record_id, candidate_id)
        elif is_task_preview:
            result = task_commands.confirm_create(record_id, candidate_id, edited_labels)
        else:
            try:
                result = task_commands.complete_by_id(candidate_id, record_id)
            except KeyError:
                return
        await _push_execution_result(device_id, result)
        records.update_status(record_id, "done")
        if candidate_id in ("remind:cancel", "task:cancel"):
            decision = "cancelled"
        elif is_remind or is_task_preview:
            decision = "confirmed"
        else:
            decision = "executed"
        if is_remind:
            intent_label = "create_reminder"
        elif is_task_preview:
            intent_label = "create_task"
        else:
            intent_label = "complete_task"
        audit.log(
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

    manager.clarify_handler = _on_clarify

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="vbadge")

    return app
