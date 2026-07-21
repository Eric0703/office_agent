"""消息信封构造(登记册 §1.2:type/version/id/ts/payload)。

gateway 与 api 共用,保证主机侧发出的每条消息信封一致。
"""

import time
import uuid
from typing import Any

PROTOCOL_VERSION = "1.0"


def make_envelope(msg_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """构造一条主机侧消息信封(id 为 uuid,ts 为 Unix 毫秒)。"""
    return {
        "type": msg_type,
        "version": PROTOCOL_VERSION,
        "id": uuid.uuid4().hex,
        "ts": int(time.time() * 1000),
        "payload": payload,
    }
