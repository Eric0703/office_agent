"""FR-03:ASR 热词/业务词表 —— 配置加载、适配层传递、真机无退化。

词表合规:真实词表只在 Owner 本机 config.yaml(gitignored);仓库测试只用
匿名占位词("WorkBuddy"、"定时任务"为用户已报告的问题词,非真实录音数据)。
真机用例为 slow(--runslow);不保存任何 Owner 真实录音。
"""

from pathlib import Path

import pytest

from agent_host.adapters.asr import FasterWhisperASR
from agent_host.config import load_config

TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "l1_synthetic"


def test_fr03_hotwords_load_from_config(tmp_path: Path) -> None:
    """配置层:asr.hotwords 从 YAML 载入;缺省为空列表。"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        'asr:\n  provider: "faster-whisper"\n  hotwords:\n    - "<业务系统名>"\n    - "定时任务"\n',
        encoding="utf-8",
    )
    config = load_config(cfg_file)
    assert config.asr.hotwords == ["<业务系统名>", "定时任务"]
    assert load_config(tmp_path / "missing.yaml").asr.hotwords == []


class _FakeSegment:
    text = "占位文本"
    avg_logprob = -0.1


class _FakeModel:
    """捕获 transcribe kwargs 的假模型(确定性,不加载权重)。"""

    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def transcribe(self, _audio_path: str, **kwargs: object) -> tuple[list[_FakeSegment], None]:
        self.kwargs = kwargs
        return [_FakeSegment()], None


def test_fr03_hotwords_passed_to_faster_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """适配层:faster-whisper ≥1.1 原生 hotwords 参数必须正确传入。"""
    fake = _FakeModel()
    asr = FasterWhisperASR(hotwords=["WorkBuddy", "定时任务"])
    monkeypatch.setattr(asr, "_load", lambda: fake)
    text, confidence = asr.transcribe("dummy.wav")
    assert text == "占位文本"
    assert confidence > 0
    assert "hotwords" in fake.kwargs
    assert "WorkBuddy" in str(fake.kwargs["hotwords"])
    assert "定时任务" in str(fake.kwargs["hotwords"])


def test_fr03_no_hotwords_no_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置词表时不得传 hotwords 参数(保持基准行为)。"""
    fake = _FakeModel()
    asr = FasterWhisperASR()
    monkeypatch.setattr(asr, "_load", lambda: fake)
    asr.transcribe("dummy.wav")
    assert "hotwords" not in fake.kwargs


def test_fr03_low_confidence_threshold_from_config(tmp_path: Path) -> None:
    """置信度阈值走配置(FR-03):asr.low_confidence_threshold 载入,缺省 0.5。"""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("asr:\n  low_confidence_threshold: 0.35\n", encoding="utf-8")
    assert load_config(cfg_file).asr.low_confidence_threshold == 0.35
    assert load_config(tmp_path / "missing.yaml").asr.low_confidence_threshold == 0.5


@pytest.mark.slow
def test_fr03_hotwords_no_regression_on_clean_l1() -> None:
    """真机无退化:带热词转写 L1 clean 音频,基准识别不得劣化(不验证 WorkBuddy 本身——
    合规语料中无该词,热词有效性由上面的传递测试与本机体验共同保证)。"""
    asr = FasterWhisperASR(model_size="small", hotwords=["WorkBuddy", "定时任务", "定时提醒"])
    text, confidence = asr.transcribe(str(TESTDATA / "audio" / "clean" / "FIELD-001.wav"))
    assert "方案" in text, f"带热词后基准识别退化:{text!r}"
    assert confidence >= 0.5
