# L1 指令合成集(主力)

规约 §5 三层黄金测试集之 L1。用途:意图路由准确率回归、指令执行的自动化回归、ASR 近讲上限基线。

## 构成

| 类别 | 条数 | 说明 |
|---|---|---|
| field(现场记录/讨论陈述) | 45 | 会议讨论、客户反馈、进度同步等陈述句,预期行为为记录整理 |
| task_command(任务指令) | 45 | 完成/新建/提醒/延期/查询/取消/优先级等动作,expected 含动作与参数 |
| experience(经验复盘口述) | 45 | 项目复盘、踩坑总结、方法论,预期行为为经验沉淀 |
| interference(干扰与边界) | 25 | 同名歧义(8)、模糊表达、闲聊(8)、中英混说(9);歧义样本 expected 为 `{"action":"clarify"}`,闲聊为 `{"action":"none","behavior":"no_route"}` |

合计 160 条,全部带标准答案标注。

## 生成方式

- 文本:`texts.jsonl`(人工编写的语料与标注,可入库)
- TTS:macOS 本地 `say`,Tingting / Meijia 双音色逐条轮换(各 80 条),`afconvert` 转 16kHz 单声道 Int16 WAV
- 噪声:Python 标准库合成粉噪(Paul Kellet 近似)+ 15% 白噪,按活跃段 RMS 叠加出 SNR≈20dB / 10dB 两档变体;固定随机种子(20260719)可复现
- 生成脚本:`scripts/gen_l1_corpus.py`(`--check` 校验数量,`--limit N` 调试)

## 文件

- `texts.jsonl` — 语料文本与标准答案(源数据)
- `labels.jsonl` — 生成产物标注(id、intent、text、expected、note、voice、duration_sec、三档文件路径)
- `audio/clean/*.wav`、`audio/snr20/*.wav`、`audio/snr10/*.wav` — 160×3 共 480 个 WAV,约 79MB,总时长约 14 分钟

合规:L1 为全合成数据,不含任何真实人声与真实个人信息,允许入库(规约 §5)。
