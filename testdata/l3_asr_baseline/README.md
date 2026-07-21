# L3 ASR 基线集(AISHELL-1 抽样)

规约 §5 三层黄金测试集之 L3。用途:WER/CER 基线与 ASR 回归(真实人声,对照 L1 合成音)。

## 来源与许可

- 数据集:**AISHELL-1**(北京希尔贝壳科技,OpenSLR SLR33),许可 **Apache 2.0**
- 下载镜像:HuggingFace datasets `shenyunhang/AISHELL-1`(OpenSLR 原始目录结构镜像)
- 转写:`aishell_transcript_v0.8.txt`(官方转写,字间空格;labels 中已去空格)

## 抽样方式

- 范围:test 集 17 个说话人目录(镜像中 S0909–S0911 缺失),候选池 6091 条
- 抽样:固定随机种子 20260719 抽 **100 条**,可复现
- 下载脚本:`scripts/fetch_l3_aishell.py`(`--n` 数量,`--mirror` 走 hf-mirror.com)

## 文件

- `labels.jsonl` — 100 条标注(id、官方转写 text、file、speaker、source URL),可入库
- `audio/*.wav` — 16kHz 单声道 Int16(经 afconvert 规整),约 16MB
- `aishell_transcript_v0.8.txt` — 官方全量转写(10MB,仅本地留档用于抽样脚本)

## 合规

L3 音频与全量转写**不入库、不二次分发**,仅内部测试用途(规约 §5);已在 `testdata/.gitignore` 中忽略。
