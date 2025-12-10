import asyncio
import json

import websockets
from websockets import ClientConnection

from astrbot import logger

from .message_handler import MessageHandler


class AuthenticationError(Exception):
    """Raised when WebSocket authentication fails."""

    pass


class QueqiaoClient:
    """
    用于与 queqiao_mcdr 插件进行 WebSocket 通信的客户端。
    """

    def __init__(
        self,
        context,
        server_name: str,
        server_uri: str,
        access_token=None,
        max_reconnect_attempts: int = 5,
        reconnect_interval: int = 60,
        message_manager=None,
    ):
        """
        初始化客户端。

        :param server_uri: WebSocket 服务器的地址 (例如 'ws://localhost:8080/ws')。
        :param access_token: 用于连接验证的访问令牌（如果服务器配置了）。
        :param max_reconnect_attempts: 最大重连尝试次数，-1 表示无限重连。
        :param reconnect_interval: 重连间隔时间（秒）。
        """
        self.websocket: ClientConnection | None = None  # 添加类型注解
        self.context = context
        self.server_name = server_name
        self.server_uri = server_uri
        self.access_token = access_token
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_interval = reconnect_interval
        self.message_manager = message_manager
        self._running_flag = True

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
            if (
                self.max_reconnect_attempts == -1
                or connection_count <= self.max_reconnect_attempts
            ):
                if await self._connect():
                    logger.info("WebSocket connection established!")
                    return True
                else:
                    connection_count += 1
                    logger.error(
                        f"Initial connection failed. Will retry in {self.reconnect_interval} seconds..."
                    )
                    await asyncio.sleep(self.reconnect_interval)
            else:
                logger.error(
                    "Maximum reconnection attempts reached. Stopping connection attempts."
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
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                logger.error(f"WebSocket connection error: {e}")
            try:
                if self.websocket is not None:
                    await self.websocket.close()
            except Exception:
                pass
            self.websocket = None
            return False

    async def send_api_request(self, api, data={}):
        """
        向服务器发送一个 API 请求。

        :param api: API 的名称，例如 'get_player_list'。
        :param data: API 需要的数据，是一个字典。
        """
        if not self.websocket:
            logger.error("错误：WebSocket 未连接。")
            return

        request = {"api": api, "data": data}

        try:
            await self.websocket.send(json.dumps(request))
            logger.info(f"已发送请求 -> API: {api}, 数据: {data}")
        except Exception as e:
            logger.error(f"发送请求失败: {e}")

    async def event_listener_loop(self):
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
                        raise Exception("WebSocket connection failed")
                if self.websocket:
                    try:
                        message = await self.websocket.recv()
                        logger.debug(f"收到服务器消息: {message}")
                        message_handler = MessageHandler(
                            self.context, message, self.message_manager
                        )
                        await message_handler.process()
                    except websockets.ConnectionClosed:
                        logger.warning("WebSocket 连接已关闭，准备重连...")
                        self.websocket = None
                    except Exception as e:
                        logger.error(f"监听消息时发生错误: {e}")
                        self.websocket = None
        except Exception as e:
            logger.error(f"Event listener loop encountered an error: {e}")
            raise
        finally:
            logger.info("Listening loop exited gracefully.")

    async def disconnect(self):
        """
        断开 WebSocket 连接。
        """
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket connection: {e}")
            finally:
                self.websocket = None
