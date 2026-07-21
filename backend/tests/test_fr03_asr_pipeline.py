"""FR-03/FR-04:真 ASR(faster-whisper small)走 L1 clean 音频,断言路由意图正确;
并断言转写后音频临时文件已删除(宪法第 3 条)。

慢速用例:默认跳过,--runslow 开启(规约 §6;模型权重缺失时首次自动下载)。
"""

from pathlib import Path

import pytest

from agent_host.adapters.asr import FasterWhisperASR
from agent_host.audio.pipeline import AudioPipeline
from agent_host.router.router import IntentKind, IntentRouter
from agent_host.store.db import init_db
from agent_host.store.repos import RecordRepo

TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "l1_synthetic"

# 三类意图各 2 条(L1 指令合成集,规约 §5)
CASES = [
    ("FIELD-001", IntentKind.FIELD_NOTE, None),
    ("FIELD-002", IntentKind.FIELD_NOTE, None),
    ("EXP-001", IntentKind.EXPERIENCE, None),
    ("EXP-002", IntentKind.EXPERIENCE, None),
    ("TASK-001", IntentKind.TASK_COMMAND, "complete_task"),
    ("TASK-003", IntentKind.TASK_COMMAND, "create_task"),
]


@pytest.fixture(scope="module")
def asr() -> FasterWhisperASR:
    return FasterWhisperASR(model_size="small")


@pytest.mark.slow
@pytest.mark.parametrize(("sample_id", "kind", "command"), CASES)
def test_fr03_fr04_asr_route_intent(
    asr: FasterWhisperASR, sample_id: str, kind: IntentKind, command: str | None
) -> None:
    text, _confidence = asr.transcribe(str(TESTDATA / "audio" / "clean" / f"{sample_id}.wav"))
    intent = IntentRouter().route(text)
    assert intent.kind == kind, f"{sample_id} 转写为:{text!r}"
    if command is not None:
        assert intent.command == command, f"{sample_id} 转写为:{text!r}"


@pytest.mark.slow
def test_fr03_audio_deleted_after_transcribe(asr: FasterWhisperASR, tmp_path: Path) -> None:
    """宪法第 3 条:转写成功后音频临时文件即删,records.audio_tmp_path 置 NULL。"""
    conn = init_db(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO devices (id, name, token_hash, paired_at)"
        " VALUES ('d1', 't', 'h', '2026-01-01T00:00:00+00:00')"
    )
    records = RecordRepo(conn)
    pipeline = AudioPipeline(asr, tmp_path / "audio_tmp", records)
    src = TESTDATA / "audio" / "clean" / "FIELD-001.wav"
    path = pipeline.save_upload(
        record_id="rec-del-1",
        device_id="d1",
        mode="field",
        started_at="2026-01-01T00:00:00+00:00",
        duration_ms=5000,
        data=src.read_bytes(),
        fmt="wav",
    )
    assert path.exists()
    text, _confidence = pipeline.transcribe_file(path)
    assert text
    pipeline.cleanup("rec-del-1")
    assert not path.exists()
    assert records.get("rec-del-1")["audio_tmp_path"] is None
