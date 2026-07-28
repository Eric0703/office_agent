"""FR-01 扩展:hello 可选 capabilities 上报 —— 只解析登记,不消费(软硬件解耦最小校准)。

dev_mode=auto_approve 原型旁路;临时数据库,不触碰本机运行时数据。
"""

import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_host.api.app import create_app
from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig
from agent_host.gateway.capabilities import DeviceCapabilities
from agent_host.gateway.manager import ConnectionManager

_UNSET = object()  # 哨兵:区分"未携带 capabilities"与"携带空值"


def _envelope(msg_type: str, payload: dict) -> dict:
    return {
        "type": msg_type,
        "version": "1.0",
        "id": uuid.uuid4().hex,
        "ts": int(time.time() * 1000),
        "payload": payload,
    }


def _hello(device_id: str, capabilities: object = _UNSET) -> dict:
    payload: dict = {
        "device_id": device_id,
        "token": "dev",
        "client": "vbadge-web",
        "client_version": "0.1.0",
        "display_profile": "400x300",
    }
    if capabilities is not _UNSET:
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


def test_fr01_hello_with_full_capabilities(
    harness: tuple[TestClient, ConnectionManager],
) -> None:
    """hello 携带完整 capabilities:hello.result ok,能力登记到设备会话,断连后清理。"""
    client, manager = harness
    caps = {
        "audio": {"formats": ["webm-opus", "pcm16"], "sample_rate": 16000, "channels": 1},
        "screen": {"type": "eink", "width": 400, "height": 300, "profile": "400x300"},
        "keys": ["record", "confirm", "back", "page"],
        "led": True,
        "haptics": False,
        "storage_mb": 512,
        "battery": True,
        "network": ["wifi", "ble"],
        "firmware_version": "1.2.3",
    }
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello("dev-cap-full", caps))
        result = ws.receive_json()
        assert result["type"] == "device.hello.result"
        assert result["payload"]["status"] == "ok"
        registered = manager.capabilities("dev-cap-full")
        assert registered is not None
        assert registered.audio is not None
        assert registered.audio.formats == ["webm-opus", "pcm16"]
        assert registered.audio.sample_rate == 16000
        assert registered.audio.channels == 1
        assert registered.screen is not None
        assert registered.screen.type == "eink"
        assert registered.screen.width == 400
        assert registered.screen.height == 300
        assert registered.screen.profile == "400x300"
        assert registered.keys == ["record", "confirm", "back", "page"]
        assert registered.led is True
        assert registered.haptics is False
        assert registered.storage_mb == 512
        assert registered.battery is True
        assert registered.network == ["wifi", "ble"]
        assert registered.firmware_version == "1.2.3"
    # 断连清理:会话内存不残留
    assert manager.capabilities("dev-cap-full") is None


def test_fr01_hello_without_capabilities(
    harness: tuple[TestClient, ConnectionManager],
) -> None:
    """hello 不带 capabilities:行为完全不变(向后兼容),会话内无能力登记。"""
    client, manager = harness
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello("dev-cap-none"))
        result = ws.receive_json()
        assert result["payload"]["status"] == "ok"
        assert manager.capabilities("dev-cap-none") is None


def test_fr01_hello_with_partial_and_unknown_capabilities(
    harness: tuple[TestClient, ConnectionManager],
) -> None:
    """capabilities 部分字段 + 未知字段:容错解析,未知字段忽略(前向兼容)。"""
    client, manager = harness
    caps = {
        "audio": {"formats": ["webm-opus"], "bitrate": 32000},  # 嵌套未知字段忽略
        "screen": {"width": 400},  # 部分字段:其余 → None
        "keys": ["record"],
        "network": "wifi",  # 类型不符:容错为空表
        "future_field": {"x": 1},  # 顶层未知字段忽略
    }
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello("dev-cap-partial", caps))
        result = ws.receive_json()
        assert result["payload"]["status"] == "ok"
        registered = manager.capabilities("dev-cap-partial")
        assert registered is not None
        assert registered.audio is not None
        assert registered.audio.formats == ["webm-opus"]
        assert registered.audio.sample_rate is None
        assert registered.screen is not None
        assert registered.screen.width == 400
        assert registered.screen.type is None
        assert registered.keys == ["record"]
        assert registered.network == []
        assert registered.led is None
        assert registered.firmware_version is None


def test_fr01_hello_with_illegal_capabilities_type(
    harness: tuple[TestClient, ConnectionManager],
) -> None:
    """capabilities 为非法类型(字符串):hello 不因此失败,容错为全空实例。"""
    client, manager = harness
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello("dev-cap-bad", "not-a-dict"))
        result = ws.receive_json()
        assert result["payload"]["status"] == "ok"
        registered = manager.capabilities("dev-cap-bad")
        assert registered is not None
        assert registered == DeviceCapabilities()  # 全空实例


def test_fr01_revoke_clears_capabilities(
    harness: tuple[TestClient, ConnectionManager],
) -> None:
    """吊销设备:capabilities 随会话清除(绑定当前连接,吊销即会话结束)。"""
    client, manager = harness
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello("dev-cap-revoke", {"keys": ["action"], "led": True}))
        assert ws.receive_json()["payload"]["status"] == "ok"
        ws.receive_json()  # state.sync
        assert manager.capabilities("dev-cap-revoke") is not None

        resp = client.post("/desk/pair/revoke", json={"device_id": "dev-cap-revoke"})
        assert resp.status_code == 200
        assert manager.capabilities("dev-cap-revoke") is None
