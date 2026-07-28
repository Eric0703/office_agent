"""设备能力描述(hello 可选扩展字段;软硬件解耦最小校准)。

只解析、登记,不消费:不做任何基于 capabilities 的逻辑分支,不落库,不校验;
未知字段忽略(前向兼容),非法/缺省输入容错为全空实例。
"""

from dataclasses import dataclass, field
from typing import Any


def _opt_int(value: Any) -> int | None:
    """容错取整:非 int(含 bool)一律 None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _opt_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _str_list(value: Any) -> list[str]:
    """容错字符串表:非 list → 空表;非 str 项丢弃。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


@dataclass
class AudioCaps:
    """音频能力:支持格式、采样率、声道数。"""

    formats: list[str] = field(default_factory=list)
    sample_rate: int | None = None
    channels: int | None = None


@dataclass
class ScreenCaps:
    """屏幕能力:类型、分辨率、显示 profile。"""

    type: str | None = None
    width: int | None = None
    height: int | None = None
    profile: str | None = None


@dataclass
class DeviceCapabilities:
    """设备能力快照(全部可选);from_dict 容错解析,缺字段 → None/空表。"""

    audio: AudioCaps | None = None
    screen: ScreenCaps | None = None
    keys: list[str] = field(default_factory=list)  # 语义键:record/confirm/back/page
    led: bool | None = None
    haptics: bool | None = None
    storage_mb: int | None = None
    battery: bool | None = None
    network: list[str] = field(default_factory=list)  # 如 ["wifi","ble"]
    firmware_version: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "DeviceCapabilities":
        """容错解析:非 dict 输入 → 全空实例;未知字段忽略(前向兼容)。"""
        if not isinstance(data, dict):
            return cls()
        raw_audio = data.get("audio")
        audio = (
            AudioCaps(
                formats=_str_list(raw_audio.get("formats")),
                sample_rate=_opt_int(raw_audio.get("sample_rate")),
                channels=_opt_int(raw_audio.get("channels")),
            )
            if isinstance(raw_audio, dict)
            else None
        )
        raw_screen = data.get("screen")
        screen = (
            ScreenCaps(
                type=_opt_str(raw_screen.get("type")),
                width=_opt_int(raw_screen.get("width")),
                height=_opt_int(raw_screen.get("height")),
                profile=_opt_str(raw_screen.get("profile")),
            )
            if isinstance(raw_screen, dict)
            else None
        )
        return cls(
            audio=audio,
            screen=screen,
            keys=_str_list(data.get("keys")),
            led=_opt_bool(data.get("led")),
            haptics=_opt_bool(data.get("haptics")),
            storage_mb=_opt_int(data.get("storage_mb")),
            battery=_opt_bool(data.get("battery")),
            network=_str_list(data.get("network")),
            firmware_version=_opt_str(data.get("firmware_version")),
        )
