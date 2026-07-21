"""ASR 抽象:faster-whisper / FunASR / 企业服务可插拔(08 §2;FR-03)。

benchmark 结论(testdata/benchmark/asr_report.md):small 档 + 固定简体
initial_prompt,L1 clean CER 6.7%,RTF≈0.52;部署必须固定简体引导。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # 避免 import 时拉起重物;faster-whisper 只在首次转写时加载
    from faster_whisper import WhisperModel

# benchmark 推荐口径(报告 §4):固定简体引导,beam_size=5,VAD 关
_ZH_INITIAL_PROMPT = "以下是普通话的句子。"


class ASRAdapter(Protocol):
    """ASR 适配器协议。"""

    def transcribe(self, audio_path: str) -> tuple[str, float]:
        """转写音频文件,返回 (文本, 置信度);音频删除由 audio 管线负责(宪法第 3 条)。"""
        ...


class MockASR:
    """固定文本 Mock:不做任何外部调用。"""

    def __init__(self, text: str = "这是一条 Mock 转写文本。", confidence: float = 0.99) -> None:
        self._text = text
        self._confidence = confidence

    def transcribe(self, audio_path: str) -> tuple[str, float]:
        """忽略音频内容,返回构造时给定的固定文本与置信度。"""
        return self._text, self._confidence


class FasterWhisperASR:
    """faster-whisper 本地实现:懒加载 small,CPU int8;webm/opus 由 PyAV 解码。

    hotwords(faster-whisper ≥1.1 原生支持):业务词表,仅改善识别,
    不改变路由与写操作的参数校验/确认语义(Owner 指令:不得借热词扩大误触)。
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        hotwords: list[str] | None = None,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._hotwords = "、".join(hotwords) if hotwords else None
        self._model: WhisperModel | None = None

    def _load(self) -> WhisperModel:
        """首次转写时才加载模型(权重缺失时由 faster-whisper 自动下载)。"""
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, audio_path: str) -> tuple[str, float]:
        """转写并返回 (拼接文本, 平均置信度);置信度由分段 avg_logprob 取 exp 估算。"""
        kwargs: dict[str, object] = {
            "language": "zh",
            "initial_prompt": _ZH_INITIAL_PROMPT,
            "beam_size": 5,
            "vad_filter": False,
        }
        if self._hotwords:
            kwargs["hotwords"] = self._hotwords
        segments, _info = self._load().transcribe(audio_path, **kwargs)  # type: ignore[call-arg]
        parts: list[str] = []
        conf_sum = 0.0
        conf_n = 0
        for seg in segments:
            parts.append(seg.text)
            if seg.avg_logprob is not None:
                conf_sum += math.exp(seg.avg_logprob)
                conf_n += 1
        text = "".join(parts).strip()
        confidence = conf_sum / conf_n if conf_n else 0.0
        return text, confidence
