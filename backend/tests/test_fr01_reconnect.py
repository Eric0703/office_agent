"""FR-01 补充:设备会话退休与 capabilities 生命周期(A1 校准,Owner 2026-07-22)。

同一 device_id 仅一个活动会话:新连接认证成功即退休旧连接;旧连接之后发来的
record.start / clarify.select / confirm.response / state.sync.request 一律无业务效果。
capabilities 绑定当前连接:每次 hello 必替换快照(未携带 → 清除);旧连接退出
经 finally 身份守卫,不得清掉新连接登记与 capabilities。
dev_mode=auto_approve,临时数据库,不触碰本机运行时数据。
"""

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from agent_host.api.app import create_app
from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig
from agent_host.gateway.manager import ConnectionManager
from agent_host.gateway.transport import TransportClosed
from agent_host.store.db import init_db
from agent_host.store.repos import BriefingRepo, CardRepo, DeviceRepo


def _envelope(msg_type: str, payload: dict) -> dict:
    return {
        "type": msg_type,
        "version": "1.0",
        "id": uuid.uuid4().hex,
        "ts": int(time.time() * 1000),
        "payload": payload,
    }


def _hello(device_id: str, capabilities: dict | None = None) -> dict:
    payload: dict = {
        "device_id": device_id,
        "token": "dev",
        "client": "vbadge-web",
        "client_version": "0.1.0",
        "display_profile": "400x300",
    }
    if capabilities is not None:
        payload["capabilities"] = capabilities
    return _envelope("device.hello", payload)


@pytest.fixture()
def harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, ConnectionManager]:
    """TestClient + 装配出的 ConnectionManager(经 monkeypatch 捕获实例,不改生产代码)。"""
    created: list[ConnectionManager] = []
    real_cls = ConnectionManager

    def _spy(*args: Any, **kwargs: Any) -> ConnectionManager:
        manager = real_cls(*args, **kwargs)
        created.append(manager)
        return manager

    monkeypatch.setattr("agent_host.api.app.ConnectionManager", _spy)
    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    client = TestClient(create_app(config))
    assert len(created) == 1
    return client, created[0]


def test_fr01_reconnect_retires_old_and_refreshes_capabilities(
    harness: tuple[TestClient, ConnectionManager],
) -> None:
    """重连退休旧连接;capabilities 每次 hello 必替换(未携带 → 清除;不同 → 替换)。"""
    client, manager = harness
    ws1 = client.websocket_connect("/ws").__enter__()
    ws1.send_json(_hello("dev-reconn", {"keys": ["action"], "network": ["wifi"]}))
    assert ws1.receive_json()["payload"]["status"] == "ok"
    ws1.receive_json()  # state.sync
    assert manager.capabilities("dev-reconn") is not None

    # 新连接认证成功 → 旧连接被退休(服务端关闭);未携带 capabilities → 清除旧值
    ws2 = client.websocket_connect("/ws").__enter__()
    ws2.send_json(_hello("dev-reconn"))
    assert ws2.receive_json()["payload"]["status"] == "ok"
    ws2.receive_json()  # state.sync
    assert manager.capabilities("dev-reconn") is None
    with pytest.raises(WebSocketDisconnect):
        ws1.receive_json()
    ws1.__exit__(None, None, None)
    # finally 身份守卫:旧连接退出后登记仍指向 ws2
    ws2_transport = manager._conns["dev-reconn"]

    # 再次重连并上报不同能力 → 替换;ws2 被退休
    ws3 = client.websocket_connect("/ws").__enter__()
    ws3.send_json(_hello("dev-reconn", {"keys": ["action", "page_up", "page_down"]}))
    assert ws3.receive_json()["payload"]["status"] == "ok"
    ws3.receive_json()  # state.sync
    caps = manager.capabilities("dev-reconn")
    assert caps is not None
    assert caps.keys == ["action", "page_up", "page_down"]
    with pytest.raises(WebSocketDisconnect):
        ws2.receive_json()
    ws2.__exit__(None, None, None)
    # 身份守卫:ws2 迟到退出不清 ws3 的登记与 capabilities
    assert manager._conns.get("dev-reconn") is not ws2_transport
    assert manager.capabilities("dev-reconn") is caps

    # 当前连接仍在推送路径上
    ws3.send_json(_envelope("state.sync.request", {}))
    pushed = ws3.receive_json()
    assert pushed["type"] == "state.sync"
    assert "cards" in pushed["payload"]
    ws3.__exit__(None, None, None)


class _StubTransport:
    """最小 Transport 桩:记录发送帧;接收即断连。"""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        raise TransportClosed()

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def test_fr01_retired_connection_messages_have_no_effect(tmp_path: Path) -> None:
    """旧连接(非登记连接)上的消息整条忽略:不 ack、不登记、不分发、无业务效果。"""
    conn = init_db(tmp_path / "t.db")
    manager = ConnectionManager(
        DeviceRepo(conn), CardRepo(conn), BriefingRepo(conn), dev_mode="auto_approve"
    )
    old, current = _StubTransport(), _StubTransport()
    manager._conns["dev-x"] = current  # 登记连接是 current;old 已退休
    handled: list[tuple] = []

    async def _clarify_spy(*args: object) -> None:
        handled.append(args)

    manager.clarify_handler = _clarify_spy
    stale_messages = [
        _envelope("record.start", {"record_id": "r-stale", "mode": "auto"}),
        _envelope("record.stop", {"record_id": "r-stale"}),
        _envelope("clarify.select", {"record_id": "r-stale", "candidate_id": "task:cancel"}),
        _envelope("confirm.response", {"confirm_id": "c-stale", "decision": "confirm"}),
        _envelope("state.sync.request", {}),
    ]
    for msg in stale_messages:
        asyncio.run(manager.on_message(old, "dev-x", msg))
    assert manager._record_modes == {}  # record.start 未登记
    assert old.sent == []  # 不 ack、不推送
    assert handled == []  # clarify_handler 未被调用
    assert current.sent == []  # 当前连接也不被旧消息牵连(state.sync 未发)

    # 对照:登记连接上的 record.start 正常生效
    asyncio.run(
        manager.on_message(
            current, "dev-x", _envelope("record.start", {"record_id": "r-live", "mode": "auto"})
        )
    )
    assert manager._record_modes.get("r-live") == "auto"
