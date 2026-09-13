from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

import websockets
from astrbot.api import logger
from websockets.exceptions import ConnectionClosed
from websockets.frames import CloseCode

from .event_handler import parse_event

if TYPE_CHECKING:
    from websockets import ClientConnection, Data

    from .message_manager import MessageManager
    from .types import JsonObject


class AuthenticationError(Exception):
    """Raised when the server rejects the name or token with a policy violation."""


class QueqiaoClient:
    """Maintain an outgoing connection to a QueQiao V2 WebSocket server."""

    def __init__(
        self,
        server_name: str,
        server_uri: str,
        access_token: str | None = None,
        max_reconnect_attempts: int = 5,
        reconnect_interval: int = 60,
        message_manager: MessageManager | None = None,
    ) -> None:
        """Create a connection owner.

        Args:
            server_name: Exact server name before URL encoding.
            server_uri: Configured ws or wss endpoint.
            access_token: Optional bearer token without the prefix.
            max_reconnect_attempts: Retries after the initial attempt; -1 means unlimited.
            reconnect_interval: Seconds between connection attempts.
            message_manager: Destination for supported events.
        """
        if max_reconnect_attempts < -1 or reconnect_interval < 0:
            raise ValueError("Invalid reconnect policy")
        self.websocket: ClientConnection | None = None
        self.server_name = server_name
        self.server_uri = server_uri
        self.access_token = access_token
        self.max_reconnect_attempts = (
            None if max_reconnect_attempts == -1 else max_reconnect_attempts
        )
        self.reconnect_interval = reconnect_interval
        self.message_manager = message_manager
        self._running_flag = True
        self._pending_api_requests: dict[str, asyncio.Future[JsonObject]] = {}
        self._send_lock = asyncio.Lock()

    def stop(self) -> None:
        """Prevent further connection attempts."""
        self._running_flag = False

    async def _connect_loop(self) -> bool:
        """Attempt the initial connection and the configured number of retries."""
        failures = 0
        while self._running_flag:
            if await self._connect():
                logger.info("QueQiao WebSocket connection established")
                return True
            if self.max_reconnect_attempts is not None and failures >= self.max_reconnect_attempts:
                logger.error("QueQiao reconnect attempts exhausted")
                return False
            failures += 1
            await asyncio.sleep(self.reconnect_interval)
        return False

    async def _connect(self) -> bool:
        """Connect and probe the socket, releasing it on failure or cancellation."""
        headers = {
            "x-client-origin": "astrbot_mcqq_lite",
            "x-self-name": quote(self.server_name, safe=""),
        }
        if self.access_token is not None:
            headers["Authorization"] = f"Bearer {self.access_token}"
        connected = False
        try:
            self.websocket = await websockets.connect(
                self.server_uri,
                ping_interval=30,
                ping_timeout=5,
                additional_headers=headers,
            )
            pong_waiter = await self.websocket.ping()
            await asyncio.wait_for(pong_waiter, timeout=2.0)
            connected = True
            return True
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == CloseCode.POLICY_VIOLATION:
                raise AuthenticationError(
                    "QueQiao rejected the server name or access token"
                ) from exc
            logger.warning("QueQiao connection closed during setup: %s", exc)
            return False
        except Exception:
            logger.exception("Failed to connect to QueQiao")
            return False
        finally:
            if not connected:
                await self.disconnect()

    def _fail_pending_api_requests(self, exc: Exception) -> None:
        """Wake API callers when their connection becomes unavailable."""
        for future in self._pending_api_requests.values():
            if not future.done():
                future.set_exception(exc)
        self._pending_api_requests.clear()

    def _decode_message_text(self, message: Data) -> str | None:
        try:
            return message.decode("utf-8") if isinstance(message, bytes) else message
        except UnicodeDecodeError:
            logger.exception("Failed to decode QueQiao WebSocket message")
            return None

    def _handle_api_response(self, payload: JsonObject) -> bool:
        if payload.get("post_type") != "response":
            return False
        echo = payload.get("echo")
        if not isinstance(echo, str):
            logger.warning("QueQiao returned an uncorrelated response: %s", payload)
            return True
        future = self._pending_api_requests.pop(echo, None)
        if future is None:
            logger.debug("Ignoring unknown QueQiao echo: %s", echo)
        elif not future.done():
            future.set_result(payload)
        return True

    async def send_api_request(
        self,
        api: str,
        data: JsonObject | None = None,
        *,
        response_timeout: float = 10.0,
    ) -> JsonObject:
        """Send a request and wait for the response with its exact echo.

        Args:
            api: QueQiao API name.
            data: API-specific payload, or None for no parameters.
            response_timeout: Maximum seconds to wait after sending.

        Returns:
            The correlated response.

        Raises:
            ConnectionError: No active connection is available.
            TimeoutError: No matching response arrived before the deadline.
        """
        echo = uuid4().hex
        request: JsonObject = {"api": api, "data": data if data is not None else {}, "echo": echo}
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        try:
            async with self._send_lock:
                websocket = self.websocket
                if websocket is None or not self._running_flag:
                    raise ConnectionError("QueQiao WebSocket is not connected")
                self._pending_api_requests[echo] = future
                await websocket.send(json.dumps(request, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout=response_timeout)
        finally:
            self._pending_api_requests.pop(echo, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # A disconnect may fail the future while send() itself is also failing.
                future.exception()

    async def event_listener_loop(self) -> None:
        """Reconnect on transport failures and stop on policy rejection or cancellation."""
        try:
            while self._running_flag:
                if self.websocket is None and not await self._connect_loop():
                    break
                websocket = self.websocket
                if websocket is None:
                    break
                try:
                    message = await websocket.recv()
                except ConnectionClosed as exc:
                    if exc.rcvd is not None and exc.rcvd.code == CloseCode.POLICY_VIOLATION:
                        raise AuthenticationError(
                            "QueQiao rejected the server name or access token",
                        ) from exc
                    logger.warning("QueQiao connection closed; reconnecting")
                    await self.disconnect()
                    continue
                except Exception:
                    logger.exception("QueQiao receive failed; reconnecting")
                    await self.disconnect()
                    continue
                text = self._decode_message_text(message)
                if text is None:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("Ignoring invalid QueQiao JSON")
                    continue
                if isinstance(payload, dict) and self._handle_api_response(payload):
                    continue
                if self.message_manager is not None and (event := parse_event(payload)) is not None:
                    self.message_manager.add_message(event)
        except Exception:
            logger.exception("QueQiao listener stopped with an error")
            raise
        finally:
            self._running_flag = False
            await self.disconnect()
            logger.info("QueQiao listener exited")

    async def disconnect(self) -> None:
        """Detach the connection before closing and fail all outstanding requests."""
        websocket, self.websocket = self.websocket, None
        self._fail_pending_api_requests(ConnectionError("QueQiao WebSocket disconnected"))
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                logger.exception("Failed to close QueQiao WebSocket")
