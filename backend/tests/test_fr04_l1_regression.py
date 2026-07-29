"""FR-04:L1 黄金集路由回归(testdata/l1_synthetic,150 条三类 + 25 条干扰)。

Gate 1 口径:路由准确率 ≥95%、误路由到 task_command 为 0(安全敏感方向零容忍)。
该指标针对**接入真实 LLM 的整机路由**——CI 不触外(宪法第 3 条),真实验收经
`--runslow` + 环境变量(LLM_API_KEY/LLM_BASE_URL/LLM_MODEL)开启,见本文件末。
CI 常态跑规则兜底回归:零容忍必须为 0(2026-07-22 规则收紧后达成),
准确率只设地板(规则是兜底,不代替 LLM 指标),混淆矩阵输出供透明检视。
"""

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from agent_host.router.router import IntentRouter

CORPUS = Path(__file__).resolve().parents[2] / "testdata" / "l1_synthetic" / "labels.jsonl"
_KIND_MAP = {"field": "field_note", "task_command": "task_command", "experience": "experience"}


def _load() -> list[tuple[str, str, str]]:
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        rows.append((d["id"], _KIND_MAP.get(d["intent"], "interference"), d["text"]))
    return rows


def _confusion(router: IntentRouter) -> tuple[Counter, float, int, dict[str, int]]:
    """返回(混淆计数, 三类准确率, field/experience 误路由 task_command 数, 各类样本数)。"""
    confusion: Counter = Counter()
    totals: dict[str, int] = Counter()
    for _id, want, text in _load():
        got = router.route(text).kind.value
        confusion[(want, got)] += 1
        totals[want] += 1
    three = ("field_note", "task_command", "experience")
    correct = sum(confusion[(c, c)] for c in three)
    total = sum(totals[c] for c in three)
    misroute = confusion[("field_note", "task_command")] + confusion[
        ("experience", "task_command")
    ]
    return confusion, correct / total, misroute, totals


def test_fr04_l1_rules_fallback_regression() -> None:
    """规则兜底回归(确定性):安全零容忍 = 0;准确率地板 + 混淆矩阵透明输出。"""
    confusion, accuracy, misroute, totals = _confusion(IntentRouter())
    print("\nL1 规则兜底混淆矩阵:", dict(confusion))
    print(f"三类准确率(规则兜底): {accuracy:.3f};各类样本数: {totals}")
    # 安全敏感方向零容忍:现场记录/经验不得被误路由为任务指令(Gate 1 硬指标)
    assert misroute == 0
    # 语料规模:三类各 50 条(FR-04 验收口径)
    assert totals == {"field_note": 50, "task_command": 50, "experience": 50, "interference": 25}
    # 规则兜底下限(不是 LLM 指标):防灾难性回归;field 召回 ≥0.9
    assert accuracy >= 0.58
    assert confusion[("field_note", "field_note")] / totals["field_note"] >= 0.9


@pytest.mark.slow()
def test_fr04_l1_llm_acceptance() -> None:
    """Gate 1 验收实测(真实 LLM):准确率 ≥95%、误路由 task_command = 0。

    需环境变量:LLM_API_KEY、LLM_BASE_URL、LLM_MODEL(另可选 LLM_ACCEPTANCE=1)。
    缺省跳过——CI 不做任何外部调用(宪法第 3 条)。
    """
    if os.environ.get("LLM_ACCEPTANCE") != "1":
        pytest.skip("真实 LLM 验收需 LLM_ACCEPTANCE=1 与 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL")
    from agent_host.adapters.llm import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        base_url=os.environ["LLM_BASE_URL"],
        model=os.environ["LLM_MODEL"],
    )
    confusion, accuracy, misroute, _totals = _confusion(IntentRouter(provider))
    print("\nL1 LLM 路由混淆矩阵:", dict(confusion))
    print(f"三类准确率(LLM): {accuracy:.3f}")
    assert misroute == 0
    assert accuracy >= 0.95
