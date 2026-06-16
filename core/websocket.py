from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import websockets
from astrbot import logger

from .api_handler import ApiHandler
from .event_handler import EventHandler

if TYPE_CHECKING:
    from astrbot.api.star import Context
    from websockets import ClientConnection, Data

    from .message_manager import MessageManager
    from .types import JsonObject, JsonValue


class AuthenticationError(Exception):
    """Raised when WebSocket authentication fails."""


class QueqiaoClient:
    """
    用于与 queqiao_mcdr 插件进行 WebSocket 通信的客户端。
    """

    def __init__(
        self,
        context: Context,
        server_name: str,
        server_uri: str,
        access_token: str | None = None,
        max_reconnect_attempts: int = 5,
        reconnect_interval: int = 60,
        message_manager: MessageManager | None = None,
    ) -> None:
        """
        初始化客户端。

        :param server_uri: WebSocket 服务器的地址 (例如 'ws://localhost:8080/ws')。
        :param access_token: 用于连接验证的访问令牌（如果服务器配置了）。
        :param max_reconnect_attempts: 最大重连尝试次数，-1 表示无限重连。
        :param reconnect_interval: 重连间隔时间（秒）。
        """
        self.websocket: ClientConnection | None = None
        self.context = context
        self.server_name = server_name
        self.server_uri = server_uri
        self.access_token = access_token
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_interval = reconnect_interval
        self.message_manager = message_manager
        self._running_flag = True
        self._pending_api_requests: dict[str, asyncio.Future[JsonObject]] = {}
        self._send_lock = asyncio.Lock()

    def stop(self) -> None:
        """停止所有后台循环。"""
        self._running_flag = False

    async def _connect_loop(self) -> bool:
        """
        尝试连接到 WebSocket 服务器，连接状态通过布尔值表示，成功时为 True。
        """
        logger.info(f"Connecting to the WebSocket server {self.server_uri} ...")
        connection_count = 0
        while self._running_flag:
            if self.max_reconnect_attempts == -1 or connection_count <= self.max_reconnect_attempts:
                if await self._connect():
                    logger.info("WebSocket connection established!")
                    return True
                connection_count += 1
                logger.error(
                    "Initial connection failed. "
                    f"Will retry in {self.reconnect_interval} seconds...",
                )
                await asyncio.sleep(self.reconnect_interval)
            else:
                logger.error(
                    "Maximum reconnection attempts reached. Stopping connection attempts.",
                )
                return False
        logger.debug("Connection loop stopped by flag.")
        return False

    async def _connect(self) -> bool:
        """Connect to the WebSocket server."""
        headers: dict[str, str] = {"x-client-origin": "astrbot_mcqq_lite"}
        if self.server_name:
            headers["x-self-name"] = self.server_name
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        self.websocket = None
        try:
            self.websocket = await websockets.connect(
                self.server_uri,
                ping_interval=30,
                ping_timeout=5,
                additional_headers=headers,
            )
            pong_waiter = await self.websocket.ping()
            await asyncio.wait_for(pong_waiter, timeout=2.0)
            logger.debug("WebSocket Authentication successful.")
            return True
        except Exception as e:
            error_msg = str(e)
            if (
                "1008" in error_msg
                or "policy violation" in error_msg
                or "Authorization Header is wrong" in error_msg
            ):
                logger.error(f"WebSocket authentication failed: {e}")
                raise AuthenticationError(f"Authentication failed: {e}") from e
            logger.error(f"WebSocket connection error: {e}")
            with suppress(Exception):
                if self.websocket is not None:
                    await self.websocket.close()
            self._fail_pending_api_requests(ConnectionError("WebSocket disconnected"))
            self.websocket = None
            return False

    def _fail_pending_api_requests(self, exc: Exception) -> None:
        """Fail all pending API calls waiting for an echoed response."""
        for future in self._pending_api_requests.values():
            if not future.done():
                future.set_exception(exc)
        self._pending_api_requests.clear()

    def _decode_message_text(self, message: Data) -> str | None:
        try:
            if isinstance(message, bytes):
                return message.decode("utf-8")
            return message
        except UnicodeDecodeError:
            logger.exception("无法解码 WebSocket 消息为文本")
            return None

    def _handle_api_response(self, payload: JsonObject) -> bool:
        if not ApiHandler.is_api_response_payload(payload):
            return False

        echo = payload.get("echo")
        if echo is None:
            logger.debug(f"收到未带 echo 的 API 响应: {payload}")
            return True

        future = self._pending_api_requests.pop(str(echo), None)
        if future is None:
            logger.debug(f"收到未知 echo 的 API 响应: {payload}")
            return True

        if not future.done():
            future.set_result(payload)
        return True

    async def send_api_request(
        self,
        api: str,
        data: JsonObject | None = None,
        *,
        wait_response: bool = True,
        response_timeout: float = 10.0,
    ) -> JsonObject | None:
        """
        向服务器发送一个 API 请求。

        :param api: API 的名称，例如 'broadcast'、'get_status'。
        :param data: API 需要的数据，是一个字典。
        """
        if not self.websocket:
            raise ConnectionError("WebSocket 未连接。")

        request: JsonObject = {"api": api, "data": data or {}}
        echo: str | None = None
        future: asyncio.Future[JsonObject] | None = None
        if wait_response:
            echo = uuid4().hex
            request["echo"] = echo
            future = asyncio.get_running_loop().create_future()
            self._pending_api_requests[echo] = future

        try:
            async with self._send_lock:
                await self.websocket.send(json.dumps(request, ensure_ascii=False))
            logger.info(f"已发送请求 -> API: {api}, 数据: {data}")
            if not wait_response or future is None:
                return None
            return await asyncio.wait_for(future, timeout=response_timeout)
        except TimeoutError:
            if echo is not None:
                self._pending_api_requests.pop(echo, None)
            raise
        except Exception as e:
            if echo is not None:
                self._pending_api_requests.pop(echo, None)
            logger.error(f"发送请求失败: {e}")
            raise

    async def event_listener_loop(self) -> None:
        """主循环：
        如果没有连接会建立连接，
        监听来自 WebSocket 服务器的消息并调用 handle_message 处理它们。
        """
        logger.debug("Starting main websocket event listener loop...")
        try:
            while self._running_flag:
                if self.websocket is None:
                    client_status = await self._connect_loop()
                    if not client_status:
                        raise ConnectionError("WebSocket connection failed")
                if self.websocket:
                    try:
                        message = await self.websocket.recv()
                        logger.debug(f"收到服务器消息: {message}")
                        text = self._decode_message_text(message)
                        if text is None:
                            continue
                        try:
                            payload = cast("JsonValue", json.loads(text))
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict) and self._handle_api_response(
                            payload,
                        ):
                            continue
                        if self.message_manager is None:
                            logger.debug("MessageManager 未初始化，跳过事件处理")
                            continue
                        event_handler = EventHandler(text, self.message_manager)
                        await event_handler.process()
                    except websockets.ConnectionClosed:
                        logger.warning("WebSocket 连接已关闭，准备重连...")
                        self._fail_pending_api_requests(
                            ConnectionError("WebSocket connection closed"),
                        )
                        self.websocket = None
                    except Exception as e:
                        logger.error(f"监听消息时发生错误: {e}")
                        self._fail_pending_api_requests(e)
                        self.websocket = None
        except Exception as e:
            logger.error(f"Event listener loop encountered an error: {e}")
            raise
        finally:
            logger.info("Listening loop exited gracefully.")

    async def disconnect(self) -> None:
        """断开 WebSocket 连接。"""
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket connection: {e}")
            finally:
                self._fail_pending_api_requests(ConnectionError("WebSocket closed"))
                self.websocket = None
