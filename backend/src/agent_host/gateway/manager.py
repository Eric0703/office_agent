"""WS 连接管理器(FR-01):hello 认证、消息分发、下发推送。

协议字段以 docs/protocol.md 为唯一事实来源。
传输细节经 gateway.transport 抽象(Transport),本模块不依赖任何 Web 框架;
hello 的 capabilities 为可选能力上报:每次 hello 必替换快照(只登记不消费);
同 device_id 仅一个活动会话:重连认证成功即退休旧连接,其消息整条忽略。
原型期 dev_mode=auto_approve:跳过配对审批,任何 hello 直通(仅限原型,见 config 注释);
正式配对照(登记册 §2.1)在后续任务卡实现。
"""

import hashlib
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from agent_host.gateway.capabilities import DeviceCapabilities
from agent_host.gateway.envelope import make_envelope
from agent_host.gateway.transport import Transport, TransportClosed
from agent_host.store.repos import BriefingRepo, CardRepo, DeviceRepo

logger = logging.getLogger(__name__)

ClarifyHandler = Callable[[str, str, str, "list[str] | None"], Awaitable[None]]

PAIR_CODE_TTL_S = 300  # 登记册 §2.1:配对码 5 分钟有效、一次性


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
        self._conns: dict[str, Transport] = {}
        self._capabilities: dict[str, DeviceCapabilities] = {}  # hello 能力上报(只登记不消费)
        self._record_modes: dict[str, str] = {}  # record.start 登记的模式,上传时取用
        self._pending_pairs: dict[str, dict[str, Any]] = {}  # 配对码 → 挂起请求(内存,一次性)
        self.clarify_handler: ClarifyHandler | None = None

    async def handle_connection(self, transport: Transport) -> None:
        """/ws 入口:首条为 device.hello 或 device.pair.request(未配对设备,登记册 §2.1)。"""
        await transport.accept()
        device_id: str | None = None
        pairing = False
        try:
            while True:
                raw = await transport.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if device_id is None:
                    msg_type = msg.get("type")
                    if msg_type == "device.pair.request":
                        # 配对请求:保持连接,等待 Owner 批准(approve_pair 推送结果);
                        # 被拒(rejected)则服务端已关闭,直接退出循环
                        pairing = await self._on_pair_request(transport, msg.get("payload") or {})
                        if not pairing:
                            return
                    elif msg_type == "device.hello":
                        device_id = await self._on_hello(transport, msg)
                        if device_id is None:
                            return
                    # 其余类型:忽略(登记册 §1.3)
                else:
                    await self.on_message(transport, device_id, msg)
        except TransportClosed:
            pass
        finally:
            # 身份守卫:同 device_id 已重连(登记指向新 transport)时,旧连接迟到退出
            # 不得清掉新连接登记与 capabilities;仅登记仍指向本连接时才清理
            if device_id is not None and self._conns.get(device_id) is transport:
                self._conns.pop(device_id, None)
                self._capabilities.pop(device_id, None)
            if pairing:
                # 挂起等待中断连:清除该连接占有的配对码,允许端侧重来
                for code in [c for c, p in self._pending_pairs.items() if p["ws"] is transport]:
                    self._pending_pairs.pop(code, None)

    async def _on_hello(self, transport: Transport, msg: dict[str, Any]) -> str | None:
        """hello 认证;返回认证通过的 device_id,失败返回 None(连接已关闭)。"""
        if msg.get("type") != "device.hello":
            return None
        payload = msg.get("payload") or {}
        device_id = payload.get("device_id") or ""
        token = payload.get("token") or ""
        if self._dev_mode == "auto_approve":
            # 仅限原型:dev_mode=auto_approve 直通,自动登记设备
            device_id = device_id or f"dev-{int(time.time())}"
            token = token or "dev"
            self._devices.create(
                device_id=device_id,
                name=payload.get("device_name") or "虚拟工牌(原型)",
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                paired_at=datetime.now(UTC).isoformat(),
            )
            self._devices.touch_last_seen(device_id, datetime.now(UTC).isoformat())
            await self._retire_current(device_id, transport)
            self._conns[device_id] = transport
            self._register_capabilities(device_id, payload)
            await self._send(
                transport,
                "device.hello.result",
                {"status": "ok", "server_time": _now_ms(), "device_id": device_id},
            )
            await self.push_state_sync(device_id)
            return device_id
        # 正式路径(FR-01):校验 device_id + token 哈希;吊销即时失效
        row = self._devices.get(device_id) if device_id else None
        if row is None:
            await self._send(
                transport,
                "device.hello.result",
                {"status": "pair_required", "server_time": _now_ms()},
            )
            await transport.close()
            return None
        if row["revoked_at"]:
            await self._send(
                transport,
                "device.hello.result",
                {"status": "revoked", "server_time": _now_ms()},
            )
            await transport.close()
            return None
        if row["token_hash"] != hashlib.sha256(token.encode()).hexdigest():
            await self._send(
                transport,
                "device.hello.result",
                {"status": "auth_failed", "server_time": _now_ms()},
            )
            await transport.close()
            return None
        self._devices.touch_last_seen(device_id, datetime.now(UTC).isoformat())
        await self._retire_current(device_id, transport)
        self._conns[device_id] = transport
        self._register_capabilities(device_id, payload)
        await self._send(
            transport,
            "device.hello.result",
            {"status": "ok", "server_time": _now_ms(), "device_id": device_id},
        )
        await self.push_state_sync(device_id)
        return device_id

    async def _retire_current(self, device_id: str, new_transport: Transport) -> None:
        """同 device_id 仅一个活动会话:覆盖登记前退休旧连接(若有且非本连接)。

        close 触发旧处理器 TransportClosed 退出;其 finally 身份守卫
        (仅登记仍指向自己才清理)不会误删新连接的登记与 capabilities。
        """
        old = self._conns.get(device_id)
        if old is not None and old is not new_transport:
            await old.close()

    def _register_capabilities(self, device_id: str, payload: dict[str, Any]) -> None:
        """hello 可选 capabilities 上报:每次 hello 必替换设备能力快照(内存,断连清理);

        携带 → 容错解析登记;未携带 → 清除旧值(能力未知)。
        不做任何基于 capabilities 的逻辑分支,不落库,不校验。
        """
        if "capabilities" in payload:
            self._capabilities[device_id] = DeviceCapabilities.from_dict(payload["capabilities"])
        else:
            self._capabilities.pop(device_id, None)

    def capabilities(self, device_id: str) -> DeviceCapabilities | None:
        """设备会话登记的 capabilities(hello 可选字段;未上报返回 None)。"""
        return self._capabilities.get(device_id)

    async def _on_pair_request(self, transport: Transport, payload: dict[str, Any]) -> bool:
        """登记 pending 配对(6 位数字码,5 分钟一次性);Owner 批准后由 approve_pair 推送结果。

        返回 True = 保持连接等待批准;False = 已拒绝并关闭,调用方应结束连接循环。
        """
        code = str(payload.get("pair_code", ""))
        if not re.fullmatch(r"\d{6}", code):
            await self._send(transport, "device.pair.result", {"status": "rejected"})
            await transport.close()
            return False
        self._pending_pairs[code] = {
            "device_name": str(payload.get("device_name", ""))[:64],
            "ws": transport,
            "ts": time.time(),
        }
        return True

    async def approve_pair(self, code: str) -> str | None:
        """Owner 批准配对:登记设备、签发 token 并推送 pair.result;返回新 device_id。

        token 只经本条消息下发,devices 表只存哈希(规约 §8);
        配对码过期(>5 分钟)按 expired 推送;码不存在/已过期返回 None(desk/CLI 映射 404)。
        """
        pending = self._pending_pairs.pop(code, None)
        if pending is None:
            return None
        if time.time() - pending["ts"] > PAIR_CODE_TTL_S:
            await self._send(pending["ws"], "device.pair.result", {"status": "expired"})
            return None
        device_id = f"dev-{uuid.uuid4().hex[:12]}"
        token = secrets.token_hex(16)
        self._devices.create(
            device_id=device_id,
            name=pending["device_name"] or "虚拟工牌",
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            paired_at=datetime.now(UTC).isoformat(),
        )
        await self._send(
            pending["ws"],
            "device.pair.result",
            {"status": "approved", "device_id": device_id, "token": token},
        )
        return device_id

    async def on_message(self, transport: Transport, device_id: str, msg: dict[str, Any]) -> None:
        """分发一条已认证设备的控制通道消息;未知类型忽略(登记册 §1.3)。

        活动性守卫:同 device_id 已重连(登记指向新连接)时,旧连接上的消息
        整条忽略——不 ack、不分发、无任何业务效果。
        """
        if self._conns.get(device_id) is not transport:
            return
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
                # edited_labels 为可选新增字段(登记册修订6;多任务预览编辑确认)
                await self.clarify_handler(
                    device_id,
                    payload.get("record_id", ""),
                    payload.get("candidate_id", ""),
                    payload.get("edited_labels"),
                )
        elif msg_type in ("card.ack", "confirm.response"):
            await self._ack(device_id, msg)
        # 其余类型:忽略

    async def push(self, device_id: str, msg_type: str, payload: dict[str, Any]) -> None:
        """向在线设备下发消息;离线则跳过(原型期无离线队列,重连走 state.sync)。

        假死容错:登记仍在但发送时才发现断连(TransportClosed),记 log 按离线跳过;
        若失败 transport 仍是该设备的登记连接(身份守卫),移除登记并清 capabilities,
        使其后续走离线路径。
        请求/应答路径(hello.result/pair.result/ack/pong)不在此列,异常语义不变。
        """
        transport = self._conns.get(device_id)
        if transport is None:
            return
        try:
            await self._send(transport, msg_type, payload)
        except TransportClosed:
            logger.info(
                "推送时发现连接已断,按离线跳过 device_id=%s msg_type=%s", device_id, msg_type
            )
            if self._conns.get(device_id) is transport:
                self._conns.pop(device_id, None)
                self._capabilities.pop(device_id, None)

    async def broadcast(self, msg_type: str, payload: dict[str, Any]) -> None:
        """向全部在线设备下发(提醒到点触发等;原型期单用户,重连恢复仍走 state.sync)。

        与 push 同一容错:单条假死连接的 TransportClosed 不影响其余设备的投递;
        移除失效登记时同步清该设备 capabilities。
        """
        for device_id, transport in list(self._conns.items()):
            try:
                await self._send(transport, msg_type, payload)
            except TransportClosed:
                logger.info("广播时发现连接已断,按离线跳过 device_id=%s", device_id)
                if self._conns.get(device_id) is transport:
                    self._conns.pop(device_id, None)
                    self._capabilities.pop(device_id, None)

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
                    "ref_task_id": r["ref_task_id"],
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
        transport = self._conns.get(device_id)
        if transport is not None and msg.get("id"):
            await self._send(transport, "ack", {"ref_id": msg["id"], "status": "ok"})

    async def _send(
        self,
        transport: Transport,
        msg_type: str,
        payload: dict[str, Any],
        msg_id: str | None = None,
    ) -> None:
        envelope = make_envelope(msg_type, payload)
        if msg_id:
            envelope["id"] = msg_id  # heartbeat pong 复用 id
        await transport.send_text(json.dumps(envelope, ensure_ascii=False))

    async def revoke(self, device_id: str) -> bool:
        """吊销设备:token 立即失效并推送 device.revoke(08 §3);设备不存在/已吊销返回 False。"""
        row = self._devices.get(device_id)
        if row is None or row["revoked_at"]:
            return False
        self._devices.revoke(device_id, datetime.now(UTC).isoformat())
        transport = self._conns.pop(device_id, None)
        self._capabilities.pop(device_id, None)  # 吊销即清除能力快照(绑定会话,会话结束)
        if transport is not None:
            await self._send(transport, "device.revoke", {})
            # 吊销即断连:旧连接不得再发送任何控制消息(FR-01)
            await transport.close()
        return True
