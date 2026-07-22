"""FR-01:正式设备接入 —— hello 校验、配对挂起/批准、吊销即时失效、音频上传 token 校验。

dev_mode=auto_approve 为原型旁路(其他用例覆盖);本文件一律正式模式(dev_mode=""),
临时数据库,不触碰本机运行时数据。
"""

import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.api.app import create_app
from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig


def _envelope(msg_type: str, payload: dict) -> dict:
    return {
        "type": msg_type,
        "version": "1.0",
        "id": uuid.uuid4().hex,
        "ts": int(time.time() * 1000),
        "payload": payload,
    }


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode=""),  # 正式模式:hello 校验 token 哈希
    )
    return TestClient(create_app(config))


def _hello(device_id: str, token: str) -> dict:
    return _envelope(
        "device.hello",
        {
            "device_id": device_id,
            "token": token,
            "client": "vbadge-web",
            "client_version": "0.1.0",
            "display_profile": "400x300",
        },
    )


def _pair_and_approve(client: TestClient, code: str = "123456") -> tuple[str, str]:
    """走一遍 pair.request → desk approve,返回 (device_id, token)。"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            _envelope("device.pair.request", {"pair_code": code, "device_name": "测试工牌"})
        )
        resp = client.post("/desk/pair/approve", json={"code": code})
        assert resp.status_code == 200
        result = ws.receive_json()
    assert result["type"] == "device.pair.result"
    assert result["payload"]["status"] == "approved"
    return result["payload"]["device_id"], result["payload"]["token"]


def test_fr01_hello_unknown_device_pair_required(client: TestClient) -> None:
    """未配对设备 hello → pair_required 并关闭(登记册 §2.1)。"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello("dev-unknown", ""))
        result = ws.receive_json()
    assert result["payload"]["status"] == "pair_required"


def test_fr01_full_pairing_flow(client: TestClient) -> None:
    """完整闭环:pair.request → 批准 → token 持久化 → hello 认证通过 + state.sync。"""
    device_id, token = _pair_and_approve(client)
    assert device_id.startswith("dev-")
    assert token
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello(device_id, token))
        hello_result = ws.receive_json()
        assert hello_result["payload"]["status"] == "ok"
        assert hello_result["payload"]["device_id"] == device_id
        sync = ws.receive_json()
        assert sync["type"] == "state.sync"
        assert "cards" in sync["payload"]


def test_fr01_wrong_token_auth_failed(client: TestClient) -> None:
    """token 哈希不匹配 → auth_failed。"""
    device_id, _token = _pair_and_approve(client)
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello(device_id, "wrong-token"))
        result = ws.receive_json()
    assert result["payload"]["status"] == "auth_failed"


def test_fr01_revoke_invalidates_immediately(client: TestClient) -> None:
    """吊销即时生效:hello 返回 revoked;音频上传 401(FR-01 验收)。"""
    device_id, token = _pair_and_approve(client)
    resp = client.post("/desk/pair/revoke", json={"device_id": device_id})
    assert resp.status_code == 200
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello(device_id, token))
        result = ws.receive_json()
    assert result["payload"]["status"] == "revoked"
    upload = client.post(
        "/audio/rec-revoked",
        headers={"X-Device-Id": device_id, "X-Token": token, "X-Audio-Format": "wav"},
        content=b"audio",
    )
    assert upload.status_code == 401
    # 重复吊销:404(幂等提示)
    assert client.post("/desk/pair/revoke", json={"device_id": device_id}).status_code == 404


def test_fr01_audio_upload_requires_token(client: TestClient) -> None:
    """正式模式音频上传:无 token 401;配对设备带 token 200(登记册 §2.2)。"""
    device_id, token = _pair_and_approve(client, "654321")
    bad = client.post(
        "/audio/rec-no-token",
        headers={"X-Device-Id": device_id, "X-Audio-Format": "wav"},
        content=b"audio",
    )
    assert bad.status_code == 401
    good = client.post(
        "/audio/rec-with-token",
        headers={
            "X-Device-Id": device_id,
            "X-Token": token,
            "X-Audio-Format": "wav",
            "X-Duration-Ms": "3000",
        },
        content=b"audio",
    )
    assert good.status_code == 200
    assert good.json()["status"] == "received"


def test_fr01_expired_pair_code(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """配对码超 5 分钟:批准按 expired 推送(登记册 §2.1)。

    TTL 置 0 构造过期(直接改模块常量,避免改 time.time 与挂起登记产生竞态)。
    """
    monkeypatch.setattr("agent_host.gateway.manager.PAIR_CODE_TTL_S", 0)
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            _envelope("device.pair.request", {"pair_code": "999999", "device_name": "旧工牌"})
        )
        resp = client.post("/desk/pair/approve", json={"code": "999999"})
        assert resp.status_code == 404
        result = ws.receive_json()
    assert result["payload"]["status"] == "expired"


def test_fr01_bad_pair_code_rejected(client: TestClient) -> None:
    """非 6 位数字配对码:rejected。"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_envelope("device.pair.request", {"pair_code": "abc", "device_name": "x"}))
        result = ws.receive_json()
    assert result["payload"]["status"] == "rejected"


def test_fr01_revoke_pushes_to_online_device(client: TestClient) -> None:
    """吊销时在线设备收到 device.revoke 推送,且旧连接被服务端关闭,不能再发指令。"""
    from starlette.websockets import WebSocketDisconnect

    device_id, token = _pair_and_approve(client)
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello(device_id, token))
        ws.receive_json()  # hello.result ok
        ws.receive_json()  # state.sync
        client.post("/desk/pair/revoke", json={"device_id": device_id})
        pushed = ws.receive_json()
        assert pushed["type"] == "device.revoke"
        # 服务端随即关闭连接:后续帧不可达,旧连接不能继续发送任何控制消息
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
