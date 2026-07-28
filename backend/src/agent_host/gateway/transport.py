"""Transport 边界:gateway 与 Web 框架之间的传输抽象(软硬件解耦最小校准)。

ConnectionManager 只依赖 Transport;fastapi 的 WebSocket 仅在本文件包装
(全 gateway 唯一允许 import fastapi 的文件,由 tests/test_arch_boundaries.py 守卫)。
断连异常统一转换为 TransportClosed,manager 不再感知 Web 框架异常类型。
"""

from typing import Protocol

from fastapi import WebSocket, WebSocketDisconnect


class TransportClosed(Exception):
    """对端断连(transport 层统一异常,屏蔽 Web 框架的断连异常类型)。"""


class Transport(Protocol):
    """一条设备控制通道的最小传输面;async 语义与 fastapi.WebSocket 对齐。"""

    async def accept(self) -> None:
        """接受连接(WS 握手)。"""
        ...

    async def receive_text(self) -> str:
        """接收一条文本帧;对端断连抛 TransportClosed。"""
        ...

    async def send_text(self, text: str) -> None:
        """发送一条文本帧;对端断连抛 TransportClosed。"""
        ...

    async def close(self, code: int = 1000) -> None:
        """服务端主动关闭(默认正常关闭码 1000)。"""
        ...


class WebSocketTransport:
    """fastapi.WebSocket → Transport;断连异常(WebSocketDisconnect)转换为 TransportClosed。"""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def accept(self) -> None:
        await self._websocket.accept()

    async def receive_text(self) -> str:
        try:
            return await self._websocket.receive_text()
        except WebSocketDisconnect as exc:
            raise TransportClosed() from exc

    async def send_text(self, text: str) -> None:
        try:
            await self._websocket.send_text(text)
        except WebSocketDisconnect as exc:
            raise TransportClosed() from exc

    async def close(self, code: int = 1000) -> None:
        await self._websocket.close(code=code)
