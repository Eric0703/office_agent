"""FR-02:离线补传边界 —— 已受理但响应/推送丢失时,duplicate 恢复补推(A1-2)。

端侧补传得到 duplicate 后,处理结果必须能恢复(服务端补推 intent.result),
不得静默丢队列。临时数据库,不触碰本机运行时数据。
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
    from agent_host.adapters.asr import MockASR

    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    return TestClient(create_app(config, asr=MockASR(text="新建任务,明天之前回复客户邮件。")))


def test_fr02_duplicate_repushes_cached_result(client: TestClient) -> None:
    """已受理但响应丢失:补传 duplicate 后,服务端补推 intent.result(结果可恢复)。"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            _envelope(
                "device.hello",
                {
                    "device_id": "dev-dup",
                    "token": "",
                    "client": "vbadge-web",
                    "client_version": "0.1.0",
                    "display_profile": "400x300",
                },
            )
        )
        ws.receive_json()  # hello.result ok
        ws.receive_json()  # state.sync

        # 原始上传被受理(假设响应在断线中丢失);BackgroundTasks 同步执行至终态
        resp = client.post(
            "/audio/rec-dup-1",
            headers={
                "X-Device-Id": "dev-dup",
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "3000",
            },
            content=b"audio",
        )
        assert resp.status_code == 200
        first = ws.receive_json()
        assert first["type"] == "intent.result"
        assert first["payload"]["status"] == "success"
        assert "已新建" in first["payload"]["title"]

        # 补传:duplicate 受理,且服务端补推同一结果(不静默吞队列)
        again = client.post(
            "/audio/rec-dup-1",
            headers={
                "X-Device-Id": "dev-dup",
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "3000",
            },
            content=b"audio",
        )
        assert again.status_code == 200
        assert again.json() == {"status": "duplicate", "record_id": "rec-dup-1"}
        repushed = ws.receive_json()
        assert repushed["type"] == "intent.result"
        assert repushed["payload"]["record_id"] == "rec-dup-1"
        assert repushed["payload"]["status"] == "success"
        assert repushed["payload"]["title"] == first["payload"]["title"]


def _hello_envelope(device_id: str, token: str) -> dict:
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


def _insert_record(db_path: Path, record_id: str, device_id: str, status: str) -> None:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO records (id, device_id, mode, started_at, duration_ms, status, created_at)"
        " VALUES (?, ?, 'auto', '2026-01-01T00:00:00+00:00', 3000, ?, '2026-01-01T00:00:00+00:00')",
        (record_id, device_id, status),
    )
    conn.commit()
    conn.close()


def test_fr02_duplicate_while_processing_no_push_then_synthesized(
    client: TestClient, tmp_path: Path
) -> None:
    """处理中收到 duplicate:不推(无可恢复终态);终态后按 records 合成通用结果补推。"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello_envelope("dev-proc", ""))
        ws.receive_json()  # hello.result ok
        ws.receive_json()  # state.sync
        _insert_record(tmp_path / "agent.db", "rec-proc", "dev-proc", "uploaded")

        # 处理中:duplicate 响应正常,但不得推送任何结果(顺序断言:下一帧只能是 pong)
        resp = client.post(
            "/audio/rec-proc",
            headers={"X-Device-Id": "dev-proc", "X-Audio-Format": "wav", "X-Duration-Ms": "3000"},
            content=b"audio",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "duplicate"
        ws.send_json(_envelope("heartbeat", {}))
        frame = ws.receive_json()
        assert frame["type"] == "heartbeat"  # 若误推 intent.result 会先于此帧到达

        # 终态(模拟处理完成):再次 duplicate → 合成通用结果补推
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "agent.db"))
        conn.execute("UPDATE records SET status = 'done' WHERE id = 'rec-proc'")
        conn.commit()
        conn.close()
        client.post(
            "/audio/rec-proc",
            headers={"X-Device-Id": "dev-proc", "X-Audio-Format": "wav", "X-Duration-Ms": "3000"},
            content=b"audio",
        )
        recovered = ws.receive_json()
        assert recovered["type"] == "intent.result"
        assert recovered["payload"]["status"] == "success"
        assert recovered["payload"]["title"] == "已处理完成"


def test_fr02_recovery_after_server_restart(client: TestClient, tmp_path: Path) -> None:
    """服务重启(结果缓存丢失):补传 duplicate 按 records 终态合成恢复,不丢结果。"""
    from agent_host.adapters.asr import MockASR
    from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig

    # 实例 1:受理并产出终态(缓存留在实例 1 内存)
    with client.websocket_connect("/ws") as ws:
        ws.send_json(_hello_envelope("dev-restart", ""))
        ws.receive_json()
        ws.receive_json()
        client.post(
            "/audio/rec-restart",
            headers={
                "X-Device-Id": "dev-restart",
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "3000",
            },
            content=b"audio",
        )
        assert ws.receive_json()["payload"]["status"] == "success"

    # 实例 2(同一数据库,全新内存缓存):duplicate → 合成恢复
    config2 = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    client2 = TestClient(create_app(config2, asr=MockASR(text="任意文本。")))
    with client2.websocket_connect("/ws") as ws2:
        ws2.send_json(_hello_envelope("dev-restart", ""))
        ws2.receive_json()
        ws2.receive_json()
        resp = client2.post(
            "/audio/rec-restart",
            headers={
                "X-Device-Id": "dev-restart",
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "3000",
            },
            content=b"audio",
        )
        assert resp.json()["status"] == "duplicate"
        recovered = ws2.receive_json()
        assert recovered["type"] == "intent.result"
        assert recovered["payload"]["record_id"] == "rec-restart"
        assert recovered["payload"]["status"] == "success"


def _pair_and_approve(client: TestClient, code: str) -> tuple[str, str]:
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            _envelope("device.pair.request", {"pair_code": code, "device_name": "测试"})
        )
        resp = client.post("/desk/pair/approve", json={"code": code})
        assert resp.status_code == 200
        result = ws.receive_json()
    return result["payload"]["device_id"], result["payload"]["token"]


def test_fr02_no_cross_device_repush(tmp_path: Path) -> None:
    """归属校验:另一台合法配对设备不得跨设备获取原设备的 intent.result。"""
    from agent_host.adapters.asr import MockASR
    from agent_host.config import AppConfig, AudioConfig, DevConfig, ProviderConfig, StoreConfig

    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="mock"),
        dev=DevConfig(dev_mode=""),  # 正式模式:两台独立配对设备
    )
    client = TestClient(create_app(config, asr=MockASR(text="新建任务,明天之前回复客户邮件。")))
    dev_a, token_a = _pair_and_approve(client, "111111")
    dev_b, token_b = _pair_and_approve(client, "222222")

    # 设备 A 上传并收到结果
    with client.websocket_connect("/ws") as ws_a:
        ws_a.send_json(_hello_envelope(dev_a, token_a))
        ws_a.receive_json()
        ws_a.receive_json()
        client.post(
            "/audio/rec-cross",
            headers={
                "X-Device-Id": dev_a,
                "X-Token": token_a,
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "3000",
            },
            content=b"audio",
        )
        assert ws_a.receive_json()["payload"]["status"] == "success"

    # 设备 B 拿同一 record_id 重传:可得 duplicate 响应,但不得收到 A 的结果
    with client.websocket_connect("/ws") as ws_b:
        ws_b.send_json(_hello_envelope(dev_b, token_b))
        ws_b.receive_json()
        ws_b.receive_json()
        resp = client.post(
            "/audio/rec-cross",
            headers={
                "X-Device-Id": dev_b,
                "X-Token": token_b,
                "X-Audio-Format": "wav",
                "X-Duration-Ms": "3000",
            },
            content=b"audio",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "duplicate"
        ws_b.send_json(_envelope("heartbeat", {}))
        frame = ws_b.receive_json()
        assert frame["type"] == "heartbeat"  # A 的结果若泄漏会先于此帧到达
