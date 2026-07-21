"""WS 连接管理器(FR-01):hello 认证、消息分发、下发推送。

协议字段以 docs/protocol.md 为唯一事实来源。
原型期 dev_mode=auto_approve:跳过配对审批,任何 hello 直通(仅限原型,见 config 注释);
正式配对照(登记册 §2.1)在后续任务卡实现。
"""

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from agent_host.gateway.envelope import make_envelope
from agent_host.store.repos import BriefingRepo, CardRepo, DeviceRepo

ClarifyHandler = Callable[[str, str, str], Awaitable[None]]


def _now_ms() -> int:
    return int(time.time() * 1000)


class ConnectionManager:
    """管理设备 WS 连接与配对生命周期;一对多(一个 device_id 一条连接)。"""

    def __init__(
        self,
        devices: DeviceRepo,
        cards: CardRepo,
        briefings: BriefingRepo,
        dev_mode: str = "",
    ) -> None:
        self._devices = devices
        self._cards = cards
        self._briefings = briefings
        self._dev_mode = dev_mode
        self._conns: dict[str, WebSocket] = {}
        self._record_modes: dict[str, str] = {}  # record.start 登记的模式,上传时取用
        self.clarify_handler: ClarifyHandler | None = None

    async def handle_connection(self, websocket: WebSocket) -> None:
        """/ws 入口:首条须为 device.hello;认证失败按协议发结果后关闭。"""
        await websocket.accept()
        device_id: str | None = None
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if device_id is None:
                    device_id = await self._on_hello(websocket, msg)
                    if device_id is None:
                        return
                else:
                    await self.on_message(device_id, msg)
        except WebSocketDisconnect:
            pass
        finally:
            if device_id is not None:
                self._conns.pop(device_id, None)

    async def _on_hello(self, websocket: WebSocket, msg: dict[str, Any]) -> str | None:
        """hello 认证;返回认证通过的 device_id,失败返回 None(连接已关闭)。"""
        if msg.get("type") != "device.hello":
            return None
        payload = msg.get("payload") or {}
        if self._dev_mode != "auto_approve":
            # 正式配对照(CLI approve)后续任务卡实现;此前一律要求配对
            await self._send(
                websocket,
                "device.hello.result",
                {"status": "pair_required", "server_time": _now_ms()},
            )
            await websocket.close()
            return None
        # 仅限原型:dev_mode=auto_approve 直通,自动登记设备
        device_id = payload.get("device_id") or f"dev-{int(time.time())}"
        token = payload.get("token") or "dev"
        self._devices.create(
            device_id=device_id,
            name=payload.get("device_name") or "虚拟工牌(原型)",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            paired_at=datetime.now(UTC).isoformat(),
        )
        self._devices.touch_last_seen(device_id, datetime.now(UTC).isoformat())
        self._conns[device_id] = websocket
        await self._send(
            websocket,
            "device.hello.result",
            {"status": "ok", "server_time": _now_ms(), "device_id": device_id},
        )
        await self.push_state_sync(device_id)
        return device_id

    async def on_message(self, device_id: str, msg: dict[str, Any]) -> None:
        """分发一条已认证设备的控制通道消息;未知类型忽略(登记册 §1.3)。"""
        msg_type = msg.get("type")
        payload = msg.get("payload") or {}
        if msg_type == "heartbeat":
            # pong 复用同一信封 id(登记册 §2.1)
            await self._send(self._conns[device_id], "heartbeat", {}, msg_id=msg.get("id"))
        elif msg_type == "record.start":
            self._record_modes[payload.get("record_id", "")] = payload.get("mode", "auto")
            await self._ack(device_id, msg)
        elif msg_type == "record.stop":
            await self._ack(device_id, msg)
        elif msg_type == "state.sync.request":
            await self.push_state_sync(device_id)
        elif msg_type == "clarify.select":
            await self._ack(device_id, msg)
            if self.clarify_handler is not None:
                await self.clarify_handler(
                    device_id, payload.get("record_id", ""), payload.get("candidate_id", "")
                )
        elif msg_type in ("card.ack", "confirm.response"):
            await self._ack(device_id, msg)
        # 其余类型:忽略

    async def push(self, device_id: str, msg_type: str, payload: dict[str, Any]) -> None:
        """向在线设备下发消息;离线则跳过(原型期无离线队列,重连走 state.sync)。"""
        websocket = self._conns.get(device_id)
        if websocket is not None:
            await self._send(websocket, msg_type, payload)

    async def push_state_sync(self, device_id: str) -> None:
        """认证成功或端侧请求时全量下发 active 卡片与当日简报(登记册 §2.6)。"""
        payload: dict[str, Any] = {
            "cards": [
                {
                    "card_id": r["id"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "body": r["body"],
                    "remind_at": r["remind_at"],
                }
                for r in self._cards.list_active()
            ]
        }
        today = datetime.now().astimezone().date().isoformat()
        briefing = self._briefings.get_by_date(today)
        if briefing is not None:
            payload["briefing"] = {
                "briefing_id": str(briefing["id"]),
                "date": briefing["date"],
                "items": json.loads(briefing["content_json"]),
            }
        await self.push(device_id, "state.sync", payload)

    def pop_record_mode(self, record_id: str) -> str | None:
        """取出并清除 record.start 登记的模式(音频上传时调用)。"""
        return self._record_modes.pop(record_id, None)

    async def _ack(self, device_id: str, msg: dict[str, Any]) -> None:
        websocket = self._conns.get(device_id)
        if websocket is not None and msg.get("id"):
            await self._send(websocket, "ack", {"ref_id": msg["id"], "status": "ok"})

    async def _send(
        self,
        websocket: WebSocket,
        msg_type: str,
        payload: dict[str, Any],
        msg_id: str | None = None,
    ) -> None:
        envelope = make_envelope(msg_type, payload)
        if msg_id:
            envelope["id"] = msg_id  # heartbeat pong 复用 id
        await websocket.send_text(json.dumps(envelope, ensure_ascii=False))

    async def pair(self, pair_code: str, device_name: str) -> None:
        """登记 pending 配对;Owner CLI approve 后下发 device.pair.result(登记册 §2.1)。"""
        raise NotImplementedError

    async def revoke(self, device_id: str) -> None:
        """吊销设备:token 立即失效并推送 device.revoke(08 §3)。"""
        raise NotImplementedError
