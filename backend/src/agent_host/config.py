"""配置加载:模板为 config.example.yaml,真实 config.yaml 不入库(规约 §4)。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml

_T = TypeVar("_T")


@dataclass(frozen=True)
class ServerConfig:
    """HTTP/WS 监听配置。"""

    host: str = "0.0.0.0"
    port: int = 8000


@dataclass(frozen=True)
class ProviderConfig:
    """LLM/ASR provider 配置;API Key 只从环境变量读取,本结构不持有 Key(规约 §4)。"""

    provider: str = "mock"
    model: str = ""
    base_url: str = ""
    api_key_env: str = "LLM_API_KEY"  # Key 所在环境变量名;config.yaml 禁写真实 Key
    timeout_s: int = 30
    allow_external: bool = False  # 宪法第 3 条例外开关,仅对 LLM 有意义
    # ASR 热词/业务词表(仅 ASR 使用;真实词表只在 Owner 本机 config.yaml,不提交)
    hotwords: list[str] = field(default_factory=list)
    # ASR 置信度阈值(仅 ASR 使用):低于即"未听清"不强行进入指令执行(FR-03)
    low_confidence_threshold: float = 0.5


@dataclass(frozen=True)
class AudioConfig:
    """音频临时区与保留策略。"""

    delete_after_transcribe: bool = True  # 宪法第 3 条:转写后即删
    tmp_dir: str = "data/audio_tmp"


@dataclass(frozen=True)
class BriefingConfig:
    """每日简报配置(FR-06)。"""

    time: str = "08:30"


@dataclass(frozen=True)
class SecurityConfig:
    """白名单指令(FR-09)。"""

    whitelist_commands: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StoreConfig:
    """持久化路径配置。"""

    db_path: str = "data/agent.db"
    notes_dir: str = "data/notes"  # 笔记草稿确认归档目录(FR-05,本机 Markdown)


@dataclass(frozen=True)
class DevConfig:
    """原型期开发开关;正式部署必须为空(严禁 auto_approve 上线)。"""

    dev_mode: str = ""  # "auto_approve" = hello 直通配对(仅限原型)


@dataclass(frozen=True)
class AppConfig:
    """应用配置根。"""

    server: ServerConfig = field(default_factory=ServerConfig)
    llm: ProviderConfig = field(default_factory=ProviderConfig)
    asr: ProviderConfig = field(default_factory=ProviderConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    briefing: BriefingConfig = field(default_factory=BriefingConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    dev: DevConfig = field(default_factory=DevConfig)


def _map(cls: type[_T], data: dict[str, Any] | None) -> _T:
    """用 YAML 节构建 dataclass,忽略未知字段(与协议兼容规则同旨:规约 §3)。"""
    if not isinstance(data, dict):
        return cls()  # type: ignore[call-arg]
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})  # type: ignore[call-arg]


def load_config(path: str | Path) -> AppConfig:
    """加载 YAML 配置;文件不存在时返回全默认(全 Mock 可跑,宪法第 7 条)。"""
    p = Path(path)
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
    if not isinstance(raw, dict):
        raw = {}
    return AppConfig(
        server=_map(ServerConfig, raw.get("server")),
        llm=_map(ProviderConfig, raw.get("llm")),
        asr=_map(ProviderConfig, raw.get("asr")),
        audio=_map(AudioConfig, raw.get("audio")),
        briefing=_map(BriefingConfig, raw.get("briefing")),
        security=_map(SecurityConfig, raw.get("security")),
        store=_map(StoreConfig, raw.get("store")),
        dev=_map(DevConfig, raw.get("dev")),
    )
