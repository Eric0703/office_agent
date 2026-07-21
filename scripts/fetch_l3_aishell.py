#!/usr/bin/env python3
"""L3 ASR 基线集下载脚本:AISHELL-1(OpenSLR SLR33,Apache 2.0)随机抽样。

- 来源镜像:HuggingFace datasets `shenyunhang/AISHELL-1`(原始 OpenSLR 目录结构镜像,
  原始 wav 逐文件存放;hf-mirror.com 域名等价可用)
- 抽样:test 集 20 个说话人目录合并,固定 seed 随机抽 N 条(默认 100)
- 转写:aishell_transcript_v0.8.txt(官方,字间空格;labels 中存去空格连续文本)
- 产物:testdata/l3_asr_baseline/audio/*.wav(16kHz 单声道)+ labels.jsonl

合规(规约 §5):L3 音频仅本地保存、不入库、不二次分发;见 testdata/.gitignore。

用法:python3 scripts/fetch_l3_aishell.py [--n 100] [--mirror]
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "testdata" / "l3_asr_baseline"
TRANSCRIPT_PATH = OUT_DIR / "aishell_transcript_v0.8.txt"

REPO = "shenyunhang/AISHELL-1"
TEST_SPEAKERS = (["S0764", "S0765", "S0766", "S0767", "S0768", "S0769", "S0770"]
                 + [f"S09{i:02d}" for i in range(1, 14)])
SAMPLE_SEED = 20260719


def http_get(url: str, dest: Path | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    if dest is not None:
        dest.write_bytes(data)
        return data
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--mirror", action="store_true",
                        help="使用 hf-mirror.com 镜像域名")
    args = parser.parse_args()
    host = "https://hf-mirror.com" if args.mirror else "https://huggingface.co"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "audio").mkdir(exist_ok=True)

    # 1. 官方转写
    if not TRANSCRIPT_PATH.exists():
        print("下载官方转写 aishell_transcript_v0.8.txt …")
        http_get(f"{host}/datasets/{REPO}/resolve/main/"
                 f"data_aishell/transcript/aishell_transcript_v0.8.txt",
                 TRANSCRIPT_PATH)
    transcripts: dict[str, str] = {}
    for line in TRANSCRIPT_PATH.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            transcripts[parts[0]] = "".join(parts[1:])
    print(f"转写条目:{len(transcripts)}")

    # 2. 拉取 test 集各说话人目录文件列表
    all_files: list[str] = []
    for spk in TEST_SPEAKERS:
        url = (f"{host}/api/datasets/{REPO}/tree/main/"
               f"data_aishell/wav/test/{spk}")
        try:
            entries = json.loads(http_get(url))
        except Exception as exc:  # 个别目录不存在则跳过
            print(f"  {spk} 列表获取失败,跳过:{exc}")
            continue
        wavs = [e["path"] for e in entries if e.get("path", "").endswith(".wav")]
        all_files.extend(wavs)
        print(f"  {spk}: {len(wavs)} 个 wav")
    if not all_files:
        print("未获取到任何文件列表,若是网络问题请尝试 --mirror")
        return 1

    # 3. 固定 seed 随机抽样
    rng = random.Random(SAMPLE_SEED)
    picked = sorted(rng.sample(all_files, min(args.n, len(all_files))))
    print(f"抽样 {len(picked)} 条(候选池 {len(all_files)})")

    # 4. 下载 + 转码 + 标注
    labels_path = OUT_DIR / "labels.jsonl"
    n_ok = 0
    with labels_path.open("w", encoding="utf-8") as lf:
        for i, path in enumerate(picked, 1):
            utt_id = Path(path).stem  # 如 BAC009S0764W0121
            text = transcripts.get(utt_id)
            if not text:
                print(f"  [{i}/{len(picked)}] {utt_id} 无转写,跳过")
                continue
            raw_wav = OUT_DIR / "audio" / f"{utt_id}.raw.wav"
            final_wav = OUT_DIR / "audio" / f"{utt_id}.wav"
            if not final_wav.exists():
                try:
                    http_get(f"{host}/datasets/{REPO}/resolve/main/{path}", raw_wav)
                except Exception as exc:
                    print(f"  [{i}/{len(picked)}] {utt_id} 下载失败:{exc}")
                    continue
                # AISHELL-1 原始即 16kHz 单声道,仍统一经 afconvert 规整一次
                proc = subprocess.run(
                    ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                     str(raw_wav), str(final_wav)], capture_output=True, text=True)
                raw_wav.unlink(missing_ok=True)
                if proc.returncode != 0:
                    print(f"  {utt_id} 转码失败:{proc.stderr.strip()}")
                    continue
            lf.write(json.dumps({
                "id": utt_id,
                "text": text,
                "file": f"audio/{utt_id}.wav",
                "speaker": utt_id[6:11],
                "source": f"{host}/datasets/{REPO}/resolve/main/{path}",
            }, ensure_ascii=False) + "\n")
            n_ok += 1
            if i % 10 == 0 or i == len(picked):
                print(f"  [{i}/{len(picked)}] 完成 {n_ok}")

    print(f"\n完成 {n_ok} 条 → {OUT_DIR}")
    print(f"标注:{labels_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
