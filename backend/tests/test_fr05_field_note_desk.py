"""FR-05/Gate 0 可观察性:L1 真机链路 —— FIELD-001 真 ASR → 路由 → 笔记草稿 → PC 工作台。

与浏览器 fake microphone 的分工(规约 §6):fake mic 只验证录音按钮/上传/UI 状态机;
本用例用仓库内合规 L1 合成音频(testdata/l1_synthetic/audio/clean/FIELD-001.wav)
验证真实 ASR/转写/草稿生成。运行时数据全部隔离在 tmp_path,不触碰本机 config.yaml
与 data/agent.db。

慢速用例:默认跳过,--runslow 开启(模型权重缺失时首次自动下载)。
"""

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_host.api.app import create_app
from agent_host.config import (
    AppConfig,
    AudioConfig,
    DevConfig,
    ProviderConfig,
    SecurityConfig,
    StoreConfig,
)

TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "l1_synthetic"
FIELD_WAV = TESTDATA / "audio" / "clean" / "FIELD-001.wav"
RECORD_ID = "gate0-field-001"

# Gate 0 阻断项:内部术语不得出现在用户可见的草稿正文/工作台数据
INTERNAL_TERMS = ("宪法", "FR-", "Mock", "规约", "error_code", "第 8 条")


@pytest.fixture()
def desk_env(tmp_path: Path) -> tuple[TestClient, Path]:
    """隔离环境:tmp 数据库 + tmp 音频临时区,真 faster-whisper small。"""
    config = AppConfig(
        store=StoreConfig(db_path=str(tmp_path / "agent.db")),
        audio=AudioConfig(delete_after_transcribe=True, tmp_dir=str(tmp_path / "audio_tmp")),
        asr=ProviderConfig(provider="faster-whisper", model="small"),
        security=SecurityConfig(
            whitelist_commands=["complete_task", "list_today_tasks", "create_task"]
        ),
        dev=DevConfig(dev_mode="auto_approve"),
    )
    app = create_app(config)
    # 与 WS hello 等价的前置:设备已登记(records.device_id 外键约束;
    # 真实流程由 dev_mode 自动登记,此处直接落库)
    conn = sqlite3.connect(str(tmp_path / "agent.db"))
    conn.execute(
        "INSERT INTO devices (id, name, token_hash, paired_at) VALUES (?, ?, ?, ?)",
        ("gate0-desk-test", "Gate0 测试设备", "x", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return TestClient(app), tmp_path


def _wait_done(db_path: Path, timeout_s: float = 180.0) -> sqlite3.Row:
    """轮询 records 直至管线终态(done/failed);后台任务在 app 事件循环中执行。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM records WHERE id = ?", (RECORD_ID,)).fetchone()
        conn.close()
        if row is not None and row["status"] in ("done", "failed"):
            return row
        time.sleep(1)
    pytest.fail(f"管线 {timeout_s}s 内未到终态")


@pytest.mark.slow
def test_fr05_field001_asr_to_draft_to_desk(desk_env: tuple[TestClient, Path]) -> None:
    client, tmp_path = desk_env

    # 1. 上传真实 L1 音频(FIELD-001:讨论陈述 → 现场记录)
    resp = client.post(
        f"/audio/{RECORD_ID}",
        headers={
            "X-Device-Id": "gate0-desk-test",
            "X-Token": "dev",
            "X-Audio-Format": "wav",
            "X-Duration-Ms": "3000",
        },
        content=FIELD_WAV.read_bytes(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "received", "record_id": RECORD_ID}

    # 2. 管线终态:转写非空、路由 field_note、状态 done
    row = _wait_done(tmp_path / "agent.db")
    assert row["status"] == "done", f"意外终态:{dict(row)}"
    assert row["transcript"], "转写文本为空"
    assert "方案" in row["transcript"], f"转写偏离预期:{row['transcript']!r}"
    assert row["intent"] == "field_note"
    assert row["confidence"] is not None and row["confidence"] >= 0.5

    # 3. 宪法第 3 条:原始音频转写后即删,临时区无残留
    assert row["audio_tmp_path"] is None
    assert list((tmp_path / "audio_tmp").iterdir()) == []

    # 4. 生成一条 pending 笔记草稿,正文无内部术语
    conn = sqlite3.connect(str(tmp_path / "agent.db"))
    conn.row_factory = sqlite3.Row
    drafts = conn.execute(
        "SELECT * FROM drafts WHERE record_id = ? AND kind = 'note' AND status = 'pending'",
        (RECORD_ID,),
    ).fetchall()
    conn.close()
    assert len(drafts) == 1
    for term in INTERNAL_TERMS:
        assert term not in drafts[0]["content_md"]

    # 5. PC 工作台可见:处理记录(已生成草稿 + 转写 + 置信度)与待确认草稿
    records = client.get("/desk/records")
    assert records.status_code == 200
    mine = [r for r in records.json() if r["transcript"] == row["transcript"]]
    assert len(mine) == 1
    assert mine[0]["status"] == "已生成草稿"
    assert mine[0]["confidence"] is not None
    for term in INTERNAL_TERMS:
        assert term not in str(records.json())

    desk_drafts = client.get("/desk/drafts")
    assert desk_drafts.status_code == 200
    pending = [d for d in desk_drafts.json() if d["kind"] == "note"]
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert "## 背景" in pending[0]["content_md"]
    for term in INTERNAL_TERMS:
        assert term not in str(desk_drafts.json())
