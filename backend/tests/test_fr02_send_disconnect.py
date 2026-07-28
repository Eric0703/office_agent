"""FR-02 补充:结果投递时断线 —— 推送失败不中断终态落库/缓存/审计,duplicate 仍可恢复。

MockASR 闩锁(threading.Event)制造"转写进行中连接已断"的确定时序:先阻塞转写,
测试关闭 WS(推送必经断线路径),再放行;另以假死 transport 单测覆盖
push/broadcast 的 TransportClosed 容错。临时数据库,不触碰本机运行时数据。
"""

import asyncio
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from agent_host.api.app import create_app
from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig
from agent_host.gateway.manager import ConnectionManager
from agent_host.gateway.transport import TransportClosed
from agent_host.store.db import init_db
from agent_host.store.repos import BriefingRepo, CardRepo, DeviceRepo

MOCK_TEXT = "新建任务,明天之前回复客户邮件。"


def _envelope(msg_type: str, payload: dict) -> dict:
    return {
        "type": msg_type,
        "version": "1.0",
        "id": uuid.uuid4().hex,
        "ts": int(time.time() * 1000),
        "payload": payload,
    }


def _hello_envelope(device_id: str) -> dict:
    return _envelope(
        "device.hello",
        {
            "device_id": device_id,
            "token": "",
            "client": "vbadge-web",
            "client_version": "0.1.0",
            "display_profile": "400x300",
        },
    )


class LatchASR:
    """闩锁 MockASR:transcribe 先报"已进入"(entered),再阻塞至 release 放行。"""

    def __init__(self, text: str, entered: threading.Event, release: threading.Event) -> None:
        self._text = text
        self._entered = entered
        self._release = release

    def transcribe(self, audio_path: str) -> tuple[str, float]:
        self._entered.set()
        assert self._release.wait(timeout=10), "测试闩锁未被释放"
        return self._text, 0.99


def _wait_terminal_status(db_path: Path, record_id: str, timeout_s: float = 5.0) -> str:
    """轮询 records 状态到终态(done/failed)并返回。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT status FROM records WHERE id = ?", (record_id,)).fetchone()
        conn.close()
        if row is not None and row[0] in ("done", "failed"):
            return str(row[0])
        time.sleep(0.02)
    raise AssertionError(f"records({record_id}) 未在期限内到达终态")


def test_fr02_result_push_during_disconnect_completes(tmp_path: Path) -> None:
    """推送必经断线:终态/缓存/审计照常完成;重连后 duplicate 补推缓存的原结果。"""
    entered = threading.Event()
    release = threading.Event()
    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    client = TestClient(create_app(config, asr=LatchASR(MOCK_TEXT, entered, release)))
    holder: dict[str, object] = {}

    def _upload() -> None:
        try:
            holder["resp"] = client.post(
                "/audio/rec-disc-1",
                headers={
                    "X-Device-Id": "dev-disc",
                    "X-Audio-Format": "wav",
                    "X-Duration-Ms": "3000",
                },
                content=b"audio",
            )
        except Exception as exc:  # 后台任务异常经 TestClient 传播:留待主线程断言
            holder["error"] = exc

    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello_envelope("dev-disc"))
        ws.receive_json()  # hello.result ok
        ws.receive_json()  # state.sync
        worker = threading.Thread(target=_upload)
        worker.start()
        assert entered.wait(timeout=5), "后台任务未进入 ASR 转写"
    # WS 已断开(会话 __exit__ 等服务端处理器结束);放行闩锁,投递必走断线路径
    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive(), "后台任务未正常结束(死锁或闩锁未放行)"
    assert "error" not in holder, f"后台任务异常泄漏:{holder.get('error')}"
    resp = holder["resp"]
    assert resp.status_code == 200
    assert resp.json() == {"status": "received", "record_id": "rec-disc-1"}

    # 终态落库与审计不因推送失败丢失
    assert _wait_terminal_status(tmp_path / "agent.db", "rec-disc-1") == "done"
    conn = sqlite3.connect(str(tmp_path / "agent.db"))
    audits = conn.execute(
        "SELECT decision, intent FROM audit_log WHERE record_id = ?", ("rec-disc-1",)
    ).fetchall()
    conn.close()
    assert audits == [("executed", "create_task")]

    # 结果已缓存:新 WS + 同 record_id duplicate → 补推原 intent.result(非合成降级)
    with client.websocket_connect("/ws") as ws2:
        ws2.send_json(_hello_envelope("dev-disc"))
        ws2.receive_json()  # hello.result ok
        ws2.receive_json()  # state.sync
        again = client.post(
            "/audio/rec-disc-1",
            headers={"X-Device-Id": "dev-disc", "X-Audio-Format": "wav", "X-Duration-Ms": "3000"},
            content=b"audio",
        )
        assert again.status_code == 200
        assert again.json() == {"status": "duplicate", "record_id": "rec-disc-1"}
        repushed = ws2.receive_json()
        assert repushed["type"] == "intent.result"
        assert repushed["payload"]["record_id"] == "rec-disc-1"
        assert repushed["payload"]["status"] == "success"
        assert "已新建" in repushed["payload"]["title"]  # 缓存原文,非"已处理完成"合成


class _DeadTransport:
    """假死 transport:任何发送都抛 TransportClosed(登记仍在、实际已断)。"""

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        raise TransportClosed

    async def send_text(self, text: str) -> None:
        raise TransportClosed

    async def close(self, code: int = 1000) -> None:
        pass


def test_fr02_push_and_broadcast_tolerate_dead_transport(tmp_path: Path) -> None:
    """假死连接:push/broadcast 捕获 TransportClosed 按离线跳过,并移除登记(身份守卫)。"""
    conn = init_db(tmp_path / "t.db")
    manager = ConnectionManager(
        DeviceRepo(conn), CardRepo(conn), BriefingRepo(conn), dev_mode="auto_approve"
    )
    dead = _DeadTransport()
    manager._conns["dev-dead"] = dead  # 假死:登记仍在,发送时才发现已断

    # push:不抛;失败 transport 仍是登记连接 → 移除,后续走离线路径
    asyncio.run(manager.push("dev-dead", "intent.result", {"record_id": "r1"}))
    assert "dev-dead" not in manager._conns
    asyncio.run(manager.push("dev-dead", "intent.result", {"record_id": "r1"}))  # 离线跳过

    # broadcast:同一容错(reminder.dismiss 等经 broadcast 的路径自动获得韧性)
    manager._conns["dev-dead"] = dead
    asyncio.run(manager.broadcast("reminder.push", {"card": {}}))
    assert "dev-dead" not in manager._conns
