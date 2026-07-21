"""音频管线:HTTP 接收、完整性校验、临时存储、转写后清理(FR-02)。

宪法第 3 条:原始音频转写完成即默认删除,records.audio_tmp_path 置 NULL;
幂等:重复 record_id 由 api 层直接返回首次受理结果(08 §1.2)。
"""

from pathlib import Path

from agent_host.adapters.asr import ASRAdapter
from agent_host.store.repos import RecordRepo

_FORMAT_EXT = {"webm-opus": ".webm", "opus": ".opus", "wav": ".wav"}


class AudioPipeline:
    """接收 → 临时存储 → 转写 → 清理;转写本身不碰 db(可放 worker 线程)。"""

    def __init__(
        self,
        asr: ASRAdapter,
        tmp_dir: str | Path,
        records: RecordRepo,
        delete_after_transcribe: bool = True,
    ) -> None:
        self._asr = asr
        self._tmp_dir = Path(tmp_dir)
        self._records = records
        self._delete_after_transcribe = delete_after_transcribe

    def save_upload(
        self,
        record_id: str,
        device_id: str,
        mode: str,
        started_at: str,
        duration_ms: int,
        data: bytes,
        fmt: str,
    ) -> Path:
        """落临时区并登记 records(status='uploaded'),返回临时文件路径。"""
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        ext = _FORMAT_EXT.get(fmt, ".bin")
        path = self._tmp_dir / f"{record_id}{ext}"
        path.write_bytes(data)
        self._records.create(
            record_id=record_id,
            device_id=device_id,
            mode=mode,
            started_at=started_at,
            duration_ms=duration_ms,
            audio_tmp_path=str(path),
        )
        return path

    def transcribe_file(self, audio_path: str | Path) -> tuple[str, float]:
        """纯转写(经 ASR 适配层),不读写 db;可安全放 worker 线程。"""
        return self._asr.transcribe(str(audio_path))

    def cleanup(self, record_id: str) -> None:
        """删除临时音频并将 records.audio_tmp_path 置 NULL(宪法第 3 条)。"""
        if not self._delete_after_transcribe:
            return
        row = self._records.get(record_id)
        if row is not None and row["audio_tmp_path"]:
            Path(row["audio_tmp_path"]).unlink(missing_ok=True)
            self._records.clear_audio_path(record_id)
