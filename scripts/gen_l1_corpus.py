#!/usr/bin/env python3
"""L1 指令合成集生成器(规约 §5 三层黄金测试集之 L1)。

- 语料文本:testdata/l1_synthetic/texts.jsonl(内置标准答案标注)
- TTS:macOS 本地 say(Tingting / Meijia 双音色轮换),afconvert 转 16kHz 单声道 WAV
- 噪声增强:Python 标准库合成粉噪(Paul Kellet 近似)+ 白噪,按目标 SNR 叠加
- 产物:audio/clean/*.wav、audio/snr20/*.wav、audio/snr10/*.wav、labels.jsonl

用法:
    python3 scripts/gen_l1_corpus.py              # 全量生成
    python3 scripts/gen_l1_corpus.py --limit 5    # 只生成前 5 条(调试用)
    python3 scripts/gen_l1_corpus.py --check      # 只校验语料数量,不合成
"""
from __future__ import annotations

import argparse
import json
import math
import random
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "testdata" / "l1_synthetic"
TEXTS_PATH = OUT_DIR / "texts.jsonl"

# 至少 2 个不同音色轮换(say -v '?' 确认可用的中文音色,名称简洁无空格)
VOICES = ["Tingting", "Meijia"]
SAMPLE_RATE = 16000
SNR_VARIANTS = [("snr20", 20.0), ("snr10", 10.0)]
# 语料规模下限(规约 §5:三类各 >=40,干扰 >=20)
MIN_COUNTS = {"field": 40, "task_command": 40, "experience": 40, "interference": 20}


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{proc.stderr.strip()}")


def synth_clean(text: str, voice: str, wav_path: Path) -> None:
    """say 合成 aiff -> afconvert 转 16kHz 单声道 Int16 WAV。"""
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
        aiff_path = Path(tmp.name)
    try:
        run(["say", "-v", voice, "-o", str(aiff_path), text])
        run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
             str(aiff_path), str(wav_path)])
    finally:
        aiff_path.unlink(missing_ok=True)


def read_wav(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE and wf.getnchannels() == 1
        raw = wf.readframes(wf.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def write_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def pink_noise(n: int, rng: random.Random) -> list[float]:
    """Paul Kellet 粉噪近似(约 -3dB/oct),混入 15% 白噪模拟办公室底噪。"""
    b0 = b1 = b2 = 0.0
    out = []
    for _ in range(n):
        white = rng.uniform(-1.0, 1.0)
        b0 = 0.99765 * b0 + white * 0.0990460
        b1 = 0.96300 * b1 + white * 0.2965164
        b2 = 0.57000 * b2 + white * 1.0526913
        pink = (b0 + b1 + b2 + white * 0.1848) * 0.25
        out.append(0.85 * pink + 0.15 * white)
    return out


def add_noise(samples: list[int], snr_db: float, rng: random.Random) -> list[int]:
    """按目标 SNR 叠加噪声。语音功率用活跃段(峰值 15% 以上样本)RMS 估计,
    避免 say 首尾静音拉低 RMS 导致实际 SNR 系统性偏高。"""
    peak = max(abs(s) for s in samples) or 1
    threshold = peak * 0.15
    active = [s for s in samples if abs(s) >= threshold] or samples
    sig_rms = math.sqrt(sum(s * s for s in active) / len(active))

    noise = pink_noise(len(samples), rng)
    noise_rms = math.sqrt(sum(x * x for x in noise) / len(noise)) or 1e-9
    scale = sig_rms / (noise_rms * 10.0 ** (snr_db / 20.0))

    mixed = []
    for s, nz in zip(samples, noise):
        v = int(round(s + nz * scale))
        mixed.append(max(-32768, min(32767, v)))
    return mixed


def load_texts() -> list[dict]:
    entries = []
    with TEXTS_PATH.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            for key in ("id", "intent", "text", "expected", "note"):
                if key not in entry:
                    raise ValueError(f"{TEXTS_PATH}:{lineno} 缺少字段 {key}")
            entries.append(entry)
    return entries


def check_counts(entries: list[dict]) -> bool:
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["intent"]] = counts.get(e["intent"], 0) + 1
    ok = True
    for intent, minimum in MIN_COUNTS.items():
        n = counts.get(intent, 0)
        status = "OK" if n >= minimum else "不足!"
        if n < minimum:
            ok = False
        print(f"  {intent}: {n} 条(下限 {minimum}){status}")
    print(f"  合计: {len(entries)} 条")
    ids = [e["id"] for e in entries]
    if len(ids) != len(set(ids)):
        print("  存在重复 id!")
        ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    parser.add_argument("--check", action="store_true", help="只校验语料数量")
    args = parser.parse_args()

    entries = load_texts()
    print(f"读取语料 {len(entries)} 条:{TEXTS_PATH}")
    if not check_counts(entries):
        return 1
    if args.check:
        return 0
    if args.limit:
        entries = entries[: args.limit]

    for variant, _ in [("clean", None)] + SNR_VARIANTS:
        (OUT_DIR / "audio" / variant).mkdir(parents=True, exist_ok=True)

    rng = random.Random(20260719)
    labels_path = OUT_DIR / "labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as lf:
        for i, entry in enumerate(entries):
            voice = VOICES[i % len(VOICES)]
            sample_id = entry["id"]
            clean_path = OUT_DIR / "audio" / "clean" / f"{sample_id}.wav"

            synth_clean(entry["text"], voice, clean_path)
            samples = read_wav(clean_path)
            duration = len(samples) / SAMPLE_RATE

            files = {"clean": f"audio/clean/{sample_id}.wav"}
            for variant, snr_db in SNR_VARIANTS:
                noisy = add_noise(samples, snr_db, rng)
                noisy_path = OUT_DIR / "audio" / variant / f"{sample_id}.wav"
                write_wav(noisy_path, noisy)
                files[variant] = f"audio/{variant}/{sample_id}.wav"

            label = {
                "id": sample_id,
                "intent": entry["intent"],
                "text": entry["text"],
                "expected": entry["expected"],
                "note": entry["note"],
                "voice": voice,
                "duration_sec": round(duration, 2),
                "files": files,
            }
            lf.write(json.dumps(label, ensure_ascii=False) + "\n")
            print(f"[{i + 1}/{len(entries)}] {sample_id} ({voice}, {duration:.1f}s)")

    print(f"\n完成。标注: {labels_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
