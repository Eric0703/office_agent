"""LLM Provider 装配与 Key 管理(规约 §4;Owner 指令:Key 只从环境变量 LLM_API_KEY 读取)。"""

from __future__ import annotations

import json

import pytest

from agent_host.adapters.llm import (
    LLMNotConfiguredError,
    MockLLM,
    OpenAICompatibleProvider,
    create_llm_adapter,
)
from agent_host.config import ProviderConfig

_REAL = {
    "provider": "openai_compatible",
    "base_url": "https://llm.example.com/v1",
    "model": "test-model",
}


class TestFactory:
    def test_default_is_mock(self) -> None:
        """默认 provider=mock,全 Mock 可运行(宪法第 7 条)。"""
        assert isinstance(create_llm_adapter(ProviderConfig()), MockLLM)

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(LLMNotConfiguredError, match="未知 LLM provider"):
            create_llm_adapter(ProviderConfig(provider="not-a-provider"))


class TestOpenAICompatibleGate:
    def test_missing_key_raises_explicit_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未配置 Key 必须显式报错,禁止静默降级;错误信息指出环境变量名。"""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(LLMNotConfiguredError, match="LLM_API_KEY"):
            create_llm_adapter(ProviderConfig(allow_external=True, **_REAL))

    def test_external_gate_blocks_real_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """allow_external 未显式开启时,即使有 Key 也拦截(宪法第 3 条)。"""
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        with pytest.raises(LLMNotConfiguredError, match="allow_external"):
            create_llm_adapter(ProviderConfig(**_REAL))

    def test_missing_base_url_or_model_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        with pytest.raises(LLMNotConfiguredError, match="base_url"):
            OpenAICompatibleProvider(base_url="", model="m")
        with pytest.raises(LLMNotConfiguredError, match="model"):
            OpenAICompatibleProvider(base_url="https://x/v1", model="")

    def test_complete_builds_request_and_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Key 经环境变量进入 Authorization 头;响应 JSON 被解析为 dict。不发起真实网络。"""
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        adapter = create_llm_adapter(ProviderConfig(allow_external=True, **_REAL))
        captured: dict[str, str] = {}

        class _FakeResp:
            def __enter__(self) -> _FakeResp:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode()

        def fake_urlopen(req: object, timeout: int) -> _FakeResp:
            captured["url"] = req.full_url  # type: ignore[attr-defined]
            captured["auth"] = req.headers["Authorization"]  # type: ignore[attr-defined]
            return _FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        assert adapter.complete("hi", {}) == {"ok": True}
        assert captured["url"] == "https://llm.example.com/v1/chat/completions"
        assert captured["auth"] == "Bearer sk-test"
