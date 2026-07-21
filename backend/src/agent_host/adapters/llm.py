"""LLM 调用抽象:provider 可切换、JSON schema 校验、脱敏开关(08 §2;宪法第 3/7 条)。

Key 管理(Owner 规约,04 §4):
- API Key 只从环境变量读取(默认 `LLM_API_KEY`),config.yaml 只保留 `api_key_env` 占位;
- 真实 Provider 未配置 Key 时必须抛 LLMNotConfiguredError,禁止静默降级为 Mock;
- 真实调用还需 config 显式 `allow_external: true`(宪法第 3 条受控例外闸门)。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

from agent_host.config import ProviderConfig

DEFAULT_API_KEY_ENV = "LLM_API_KEY"


class LLMNotConfiguredError(RuntimeError):
    """真实 LLM Provider 缺少必要配置(Key/base_url/model/闸门)时抛出;信息必须可行动。"""


class LLMProviderError(RuntimeError):
    """真实 LLM 调用失败(网络/HTTP/响应解析)时抛出;不得包含任何凭据。"""


class LLMAdapter(Protocol):
    """LLM 适配器协议;router 只经本接口调模型(08 §2)。"""

    def complete(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """按 JSON schema 返回结构化输出;出域/脱敏由实现方按宪法第 3 条执行。"""
        ...


class MockLLM:
    """规则式 Mock:不做任何外部调用,返回固定结果,保证全 Mock 可运行。"""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self._response: dict[str, Any] = response or {
            "intent": "unknown",
            "confidence": 0.0,
            "entities": {},
        }

    def complete(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """忽略 prompt,返回构造时给定的固定响应。"""
        return dict(self._response)


class OpenAICompatibleProvider:
    """OpenAI 兼容接口(/chat/completions)的预留 Provider。

    仅用标准库 urllib,不引入第三方 HTTP 依赖(规约 §4)。
    构造时即校验 base_url/model/环境变量 Key,缺失立即报错(fail fast)。
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        timeout_s: int = 30,
    ) -> None:
        if not base_url:
            raise LLMNotConfiguredError(
                "openai_compatible 需要 base_url(请在 config.yaml 的 llm.base_url 配置)"
            )
        if not model:
            raise LLMNotConfiguredError(
                "openai_compatible 需要 model(请在 config.yaml 的 llm.model 配置)"
            )
        key = os.environ.get(api_key_env, "").strip()
        if not key:
            raise LLMNotConfiguredError(
                f"未配置 API Key:请设置环境变量 {api_key_env}"
                "(可写入本地 .env,已 gitignore);config.yaml 禁止存放真实 Key"
            )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = key
        self._timeout = timeout_s

    def complete(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """调用 /chat/completions 并解析 JSON 结构化输出;失败抛 LLMProviderError。"""
        body = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:200]
            raise LLMProviderError(f"LLM HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise LLMProviderError(f"LLM 调用失败: {e}") from e
        try:
            content = payload["choices"][0]["message"]["content"]
            return dict(json.loads(content))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise LLMProviderError(f"LLM 响应格式异常: {e}") from e


def create_llm_adapter(config: ProviderConfig) -> LLMAdapter:
    """按配置装配 LLM 适配器;默认 mock,全 Mock 可运行(宪法第 7 条)。

    真实 provider 两道闸门(任一不过即显式报错,绝不静默降级):
    1. `allow_external` 必须显式开启(宪法第 3 条受控例外);
    2. `api_key_env` 指向的环境变量必须存在非空 Key。
    """
    if config.provider == "mock":
        return MockLLM()
    if config.provider == "openai_compatible":
        if not config.allow_external:
            raise LLMNotConfiguredError(
                "真实 LLM 调用被宪法第 3 条闸门拦截:"
                "请在 config.yaml 显式设置 llm.allow_external: true"
            )
        return OpenAICompatibleProvider(
            base_url=config.base_url,
            model=config.model,
            api_key_env=config.api_key_env or DEFAULT_API_KEY_ENV,
            timeout_s=config.timeout_s,
        )
    raise LLMNotConfiguredError(
        f"未知 LLM provider: {config.provider!r}(可选: mock / openai_compatible)"
    )
