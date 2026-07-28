"""FR-08:clarify 取消 = 完整服务端终态(Owner 2026-07-22)。

task:cancel / remind:cancel:不执行任何候选;records 终态 done;audit decision='cancelled';
终态 intent.result("已取消")入缓存——离线凭据可出队,duplicate 恢复重放终态,
不会回到候选页。临时数据库,不触碰本机运行时数据。
"""

import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.adapters.task import MockTaskAdapter
from agent_host.api.app import create_app
from agent_host.audit.logger import AuditLogger
from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig
from agent_host.core.processing import ProcessingDeps, on_clarify, process_text
from agent_host.router.router import IntentRouter
from agent_host.skills.experience import ExperienceSkill
from agent_host.skills.field_note import FieldNoteSkill
from agent_host.skills.reminder import ReminderSkill
from agent_host.skills.task_command import TaskCommandSkill
from agent_host.store.db import init_db
from agent_host.store.repos import AuditRepo, CardRepo, DeviceRepo, DraftRepo, RecordRepo, TaskRepo

DEVICE_ID = "dev-c"


def _envelope(msg_type: str, payload: dict) -> dict:
    return {
        "type": msg_type,
        "version": "1.0",
        "id": uuid.uuid4().hex,
        "ts": int(time.time() * 1000),
        "payload": payload,
    }


@pytest.fixture()
def env(tmp_path: Path) -> tuple[ProcessingDeps, sqlite3.Connection, list[dict]]:
    """真实 repos/router/skills 装配的 ProcessingDeps(仅 cache_result 用内存桩)。"""
    conn = init_db(tmp_path / "t.db")
    DeviceRepo(conn).create(
        device_id=DEVICE_ID,
        name="测试设备",
        token_hash="x",
        paired_at="2026-01-01T00:00:00+00:00",
    )
    cached: list[dict] = []
    deps = ProcessingDeps(
        records=RecordRepo(conn),
        router=IntentRouter(),
        field_notes=FieldNoteSkill(DraftRepo(conn)),
        experience=ExperienceSkill(DraftRepo(conn)),
        reminders=ReminderSkill(CardRepo(conn), TaskRepo(conn)),
        task_commands=TaskCommandSkill(MockTaskAdapter(TaskRepo(conn)), CardRepo(conn)),
        audit=AuditLogger(AuditRepo(conn)),
        low_confidence_threshold=0.5,
        cache_result=cached.append,
    )
    return deps, conn, cached


def _seed_record(deps: ProcessingDeps, record_id: str) -> None:
    deps.records.create(
        record_id=record_id,
        device_id=DEVICE_ID,
        mode="auto",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=3000,
    )


def _clarify_then_cancel(
    deps: ProcessingDeps, record_id: str, text: str
) -> tuple[list[dict], object]:
    """文本进 clarify 中间态,再 task:cancel;返回(cached, cancel outcome)。"""
    _seed_record(deps, record_id)
    outcome = process_text(
        deps, record_id=record_id, text=text, confidence=0.99, device_id=DEVICE_ID
    )
    assert outcome.messages[0].payload["status"] == "clarify"
    cancel = on_clarify(
        deps, device_id=DEVICE_ID, record_id=record_id, candidate_id="task:cancel"
    )
    assert cancel is not None
    return cancel


def test_fr08_ambiguous_clarify_cancel_is_terminal(
    env: tuple[ProcessingDeps, sqlite3.Connection, list],
) -> None:
    """歧义 clarify 取消:不执行候选;records done;audit cancelled(意图取路由登记);
    缓存为终态"已取消"(duplicate 恢复重放终态,不卡候选页)。"""
    deps, conn, cached = env
    tasks = TaskRepo(conn)
    tasks.insert(title="回复客户邮件")
    tasks.insert(title="回复老板消息")
    cancel = _clarify_then_cancel(deps, "rec-c-ambig", "把回复标记为已完成。")

    payload = cancel.messages[0].payload
    assert payload["status"] == "success"
    assert payload["title"] == "已取消"
    # 不执行任何候选:两个任务都仍 open
    rows = conn.execute("SELECT status FROM tasks ORDER BY created_at").fetchall()
    assert [r[0] for r in rows] == ["open", "open"]
    # records 终态 + audit cancelled(意图标签取 records 已登记的路由意图)
    status = conn.execute(
        "SELECT status FROM records WHERE id = 'rec-c-ambig'"
    ).fetchone()[0]
    assert status == "done"
    audits = conn.execute(
        "SELECT decision, intent FROM audit_log WHERE record_id = 'rec-c-ambig'"
    ).fetchall()
    assert [tuple(r) for r in audits] == [("cancelled", "complete_task")]
    # 缓存顶为终态(澄清中间态在前,取消终态在后)——duplicate 恢复只重放终态
    assert cached[-1] == payload
    assert cached[-1]["status"] == "success"


def test_fr08_preview_clarify_cancel_creates_nothing(
    env: tuple[ProcessingDeps, sqlite3.Connection, list],
) -> None:
    """多任务预览取消:预览挂起弹掉,零任务创建,audit cancelled。"""
    deps, conn, cached = env
    cancel = _clarify_then_cancel(
        deps, "rec-c-preview", "下午要准备会议论文,另外把使用说明整理一下。"
    )
    assert cancel.messages[0].payload["title"] == "已取消"
    count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 0
    audits = conn.execute(
        "SELECT decision, intent FROM audit_log WHERE record_id = 'rec-c-preview'"
    ).fetchall()
    assert [tuple(r) for r in audits] == [("cancelled", "create_task")]


def test_fr08_remind_cancel_regression(
    env: tuple[ProcessingDeps, sqlite3.Connection, list],
) -> None:
    """remind:cancel 既有语义回归:不建卡,audit cancelled。"""
    deps, conn, cached = env
    _seed_record(deps, "rec-c-remind")
    outcome = process_text(
        deps, record_id="rec-c-remind", text="提醒我十点交方案。",
        confidence=0.99, device_id=DEVICE_ID,
    )
    assert outcome.messages[0].payload["status"] == "clarify"  # 缺日期 → 候选确认
    cancel = on_clarify(
        deps, device_id=DEVICE_ID, record_id="rec-c-remind", candidate_id="remind:cancel"
    )
    assert cancel is not None
    assert cancel.messages[0].payload["status"] == "success"  # 终态,可出队
    assert "未创建" in cancel.messages[0].payload["title"]
    count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    assert count == 0
    audits = conn.execute(
        "SELECT decision, intent FROM audit_log WHERE record_id = 'rec-c-remind'"
    ).fetchall()
    assert [tuple(r) for r in audits] == [("cancelled", "create_reminder")]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient(MockASR 歧义文本;预置两个相似任务)。"""
    from agent_host.adapters.asr import MockASR

    db_path = tmp_path / "agent.db"
    conn = init_db(db_path)
    tasks = TaskRepo(conn)
    tasks.insert(title="回复客户邮件")
    tasks.insert(title="回复老板消息")
    conn.close()
    config = AppConfig(
        store=StoreConfig(db_path=str(db_path)),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    return TestClient(create_app(config, asr=MockASR(text="把回复标记为已完成。")))


def test_fr08_clarify_cancel_duplicate_replays_terminal(client: TestClient) -> None:
    """端到端:clarify → task:cancel → "已取消";断线重连 + duplicate 上传,
    补推的是"已取消"终态,不会回到候选页。"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            _envelope(
                "device.hello",
                {
                    "device_id": "dev-cancel",
                    "token": "",
                    "client": "vbadge-web",
                    "client_version": "0.1.0",
                    "display_profile": "400x300",
                },
            )
        )
        ws.receive_json()  # hello.result ok
        ws.receive_json()  # state.sync

        resp = client.post(
            "/audio/rec-cancel-1",
            headers={"X-Device-Id": "dev-cancel", "X-Audio-Format": "wav",
                     "X-Duration-Ms": "3000"},
            content=b"audio",
        )
        assert resp.status_code == 200
        clarify = ws.receive_json()
        assert clarify["payload"]["status"] == "clarify"

        ws.send_json(
            _envelope(
                "clarify.select", {"record_id": "rec-cancel-1", "candidate_id": "task:cancel"}
            )
        )
        ws.receive_json()  # ack
        cancelled = ws.receive_json()
        assert cancelled["type"] == "intent.result"
        assert cancelled["payload"]["status"] == "success"
        assert cancelled["payload"]["title"] == "已取消"

    # 断线重连 + duplicate 上传:补推终态"已取消"(不卡候选页)
    with client.websocket_connect("/ws") as ws2:
        ws2.send_json(
            _envelope(
                "device.hello",
                {
                    "device_id": "dev-cancel",
                    "token": "",
                    "client": "vbadge-web",
                    "client_version": "0.1.0",
                    "display_profile": "400x300",
                },
            )
        )
        ws2.receive_json()  # hello.result ok
        ws2.receive_json()  # state.sync
        again = client.post(
            "/audio/rec-cancel-1",
            headers={"X-Device-Id": "dev-cancel", "X-Audio-Format": "wav",
                     "X-Duration-Ms": "3000"},
            content=b"audio",
        )
        assert again.status_code == 200
        assert again.json()["status"] == "duplicate"
        repushed = ws2.receive_json()
        assert repushed["type"] == "intent.result"
        assert repushed["payload"]["status"] == "success"
        assert repushed["payload"]["title"] == "已取消"


def test_fr08_clarify_select_minimal_validation(tmp_path: Path) -> None:
    """clarify.select 最小安全校验:归属/缓存态/候选合法(取消 id 类型匹配),否则忽略。

    忽略 = 不推送、不改库、不审计;ack 仅为协议接收确认,不代表业务效果。
    """
    from agent_host.adapters.asr import MockASR

    db_path = tmp_path / "agent.db"
    conn = init_db(db_path)
    tasks = TaskRepo(conn)
    tasks.insert(title="回复客户邮件")
    tasks.insert(title="回复老板消息")
    conn.close()
    client = TestClient(
        create_app(
            AppConfig(
                store=StoreConfig(db_path=str(db_path)),
                audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
                asr=ProviderConfig(provider="mock"),
                dev=DevConfig(dev_mode="auto_approve"),
            ),
            asr=MockASR(text="把回复标记为已完成。"),
        )
    )

    def _hello(ws: object, device_id: str) -> None:
        ws.send_json(
            _envelope(
                "device.hello",
                {
                    "device_id": device_id,
                    "token": "",
                    "client": "vbadge-web",
                    "client_version": "0.1.0",
                    "display_profile": "400x300",
                },
            )
        )
        ws.receive_json()  # hello.result ok
        ws.receive_json()  # state.sync

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        _hello(ws, "dev-v")
        _hello(other, "dev-other")
        resp = client.post(
            "/audio/rec-val-1",
            headers={"X-Device-Id": "dev-v", "X-Audio-Format": "wav",
                     "X-Duration-Ms": "3000"},
            content=b"audio",
        )
        assert resp.status_code == 200
        clarify = ws.receive_json()
        assert clarify["payload"]["status"] == "clarify"

        # ① 他人设备的记录:dev-other 发送 → 忽略
        other.send_json(
            _envelope("clarify.select", {"record_id": "rec-val-1", "candidate_id": "task:cancel"})
        )
        other.receive_json()  # ack(协议接收确认)
        # ② 未下发的候选 id → 忽略
        ws.send_json(
            _envelope("clarify.select", {"record_id": "rec-val-1", "candidate_id": "cand-x"})
        )
        ws.receive_json()  # ack
        # ③ 类型不匹配的取消 id(任务 clarify 发 remind:cancel)→ 忽略
        ws.send_json(
            _envelope("clarify.select", {"record_id": "rec-val-1", "candidate_id": "remind:cancel"})
        )
        ws.receive_json()  # ack
        # ④ 不存在的 record_id → 忽略
        ws.send_json(
            _envelope("clarify.select", {"record_id": "rec-none", "candidate_id": "task:cancel"})
        )
        ws.receive_json()  # ack

        # 以上全部无业务效果:records 仍中间态、零审计;下一条消息必须是合法取消的终态
        ws.send_json(
            _envelope("clarify.select", {"record_id": "rec-val-1", "candidate_id": "task:cancel"})
        )
        ws.receive_json()  # ack
        terminal = ws.receive_json()
        assert terminal["type"] == "intent.result"
        assert terminal["payload"]["title"] == "已取消"

        # ⑤ 终态后缓存已非 clarify:再发 clarify.select → 忽略(审计仅一条 cancelled)
        ws.send_json(
            _envelope("clarify.select", {"record_id": "rec-val-1", "candidate_id": "task:cancel"})
        )
        ws.receive_json()  # ack

    # 数据库终态核对:仅合法取消产生一条 cancelled;任务仍全部 open
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    audits = conn.execute(
        "SELECT decision FROM audit_log WHERE record_id = 'rec-val-1'"
    ).fetchall()
    assert [tuple(r) for r in audits] == [("cancelled",)]
    status = conn.execute(
        "SELECT status FROM records WHERE id = 'rec-val-1'"
    ).fetchone()[0]
    assert status == "done"
    open_count = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'open'").fetchone()[0]
    assert open_count == 2
    conn.close()
