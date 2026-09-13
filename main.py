import asyncio
from collections.abc import AsyncGenerator

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.event import filter as event_filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr
from websockets.exceptions import InvalidURI
from websockets.uri import parse_uri

from .core.api import QueqiaoApi
from .core.message_manager import MessageManager
from .core.types import JsonObject, JsonValue
from .core.websocket import QueqiaoClient

MCTELL_PART_COUNT = 2


def _config_section(value: JsonValue) -> JsonObject:
    if isinstance(value, dict):
        return value
    return {}


def _config_string(section: JsonObject, key: str) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"Configuration field {key} must be a string")
    return value if value.strip() else None


def _config_int(section: JsonObject, key: str, default: int) -> int:
    value = section.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _config_str_list(section: JsonObject, key: str) -> list[str]:
    value = section.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


class Queqiaolite(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._tasks: list[asyncio.Task[None]] = []
        self.message_manager: MessageManager | None = None
        self.queqiao_api: QueqiaoApi | None = None
        self.queqiao_client: QueqiaoClient | None = None

    async def initialize(self) -> None:
        """Validate configuration before starting the plugin's background services."""
        # queqiao_server
        queqiao_server = _config_section(self.config.get("queqiao_server", {}))
        server_name = _config_string(queqiao_server, "server_name")
        server_uri = _config_string(queqiao_server, "server_uri")
        access_token = _config_string(queqiao_server, "access_token")
        if server_name is None or server_uri is None:
            raise ValueError("QueQiao server_name and server_uri must not be blank")
        try:
            parse_uri(server_uri)
        except InvalidURI as exc:
            raise ValueError("server_uri must be a valid ws:// or wss:// URL") from exc

        # connection_policy
        connection_policy = _config_section(self.config.get("connection_policy", {}))
        max_reconnect_attempts = _config_int(
            connection_policy,
            "max_reconnect_attempts",
            5,
        )
        reconnect_interval = _config_int(connection_policy, "reconnect_interval", 60)
        if max_reconnect_attempts < -1 or reconnect_interval < 0:
            raise ValueError("Reconnect attempts must be >= -1 and interval must be >= 0")

        # notification
        notification = _config_section(self.config.get("notification", {}))
        enabled_events = _config_str_list(notification, "enabled_events")
        umo_list = _config_str_list(notification, "umo_list")
        min_merge_window = _config_int(notification, "min_merge_window", 10)
        max_merge_window = _config_int(notification, "max_merge_window", 60)

        # Map configuration labels to protocol event types.
        events_map = {
            "玩家加入|PlayerJoinEvent": "player_join",
            "玩家退出|PlayerQuitEvent": "player_quit",
            "玩家死亡|PlayerDeathEvent": "player_death",
            "玩家成就|PlayerAchievementEvent": "player_achievement",
            "玩家聊天|PlayerChatEvent": "player_chat",
            "玩家命令|PlayerCommandEvent": "player_command",
        }
        enabled_sub_types = [events_map[event] for event in enabled_events if event in events_map]
        logger.info(enabled_sub_types)

        # Keep invalid merge settings from entering the runtime state.
        if min_merge_window < 0 or max_merge_window < 0 or max_merge_window <= min_merge_window:
            logger.warning(
                "Invalid merge windows; using max_merge_window=60 and min_merge_window=10",
            )
            max_merge_window = 60
            min_merge_window = 10

        # Start the notification service.
        self.message_manager = MessageManager(
            context=self.context,
            enabled_sub_types=enabled_sub_types,
            umo_list=umo_list,
            min_merge_window=min_merge_window,
            max_merge_window=max_merge_window,
        )
        task_message_manager_loop = asyncio.create_task(
            self.message_manager.message_manager_loop(),
            name="task_message_manager_loop",
        )
        self._tasks.append(task_message_manager_loop)

        # Start the WebSocket listener.
        self.queqiao_client = QueqiaoClient(
            server_name=server_name,
            server_uri=server_uri,
            access_token=access_token,
            max_reconnect_attempts=max_reconnect_attempts,
            reconnect_interval=reconnect_interval,
            message_manager=self.message_manager,
        )
        self.queqiao_api = QueqiaoApi(self.queqiao_client)
        task_event_listener_loop = asyncio.create_task(
            self.queqiao_client.event_listener_loop(),
            name="task_event_listener_loop",
        )
        self._tasks.append(task_event_listener_loop)

    @event_filter.command("mc")
    async def mc(
        self,
        event: AstrMessageEvent,
        message: GreedyStr,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """不带参数查询在线人数，带参数发送服务器广播。"""
        if self.queqiao_api is None or self.message_manager is None:
            yield event.plain_result("QueQiao 客户端还没有初始化。")
            return

        message_text = str(message).strip()
        if not message_text:
            yield event.plain_result(await self._query_online_count())
            return

        try:
            response = await self.queqiao_api.broadcast(message_text)
        except TimeoutError:
            yield event.plain_result("广播发送超时，服务端没有返回确认。")
            return
        except Exception as e:
            logger.exception("Failed to broadcast through QueQiao")
            yield event.plain_result(f"广播发送失败：{type(e).__name__}: {e}")
            return

        yield event.plain_result(self.message_manager.build_broadcast_result(response))

    @event_filter.command("mctell")
    async def mctell(
        self,
        event: AstrMessageEvent,
        message: GreedyStr,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """向指定玩家发送私聊消息。"""
        if self.queqiao_api is None or self.message_manager is None:
            yield event.plain_result("QueQiao 客户端还没有初始化。")
            return

        command_text = str(message).strip()
        parts = command_text.split(maxsplit=1)
        if len(parts) != MCTELL_PART_COUNT or not parts[0].strip() or not parts[1].strip():
            yield event.plain_result("用法：/mctell <玩家ID或UUID> <聊天内容>")
            return

        target = parts[0].strip()
        message_text = parts[1].strip()
        try:
            response = await self.queqiao_api.send_private_msg(target, message_text)
        except TimeoutError:
            yield event.plain_result("私聊发送超时，服务端没有返回确认。")
            return
        except Exception as e:
            logger.exception("Failed to send a QueQiao private message")
            yield event.plain_result(f"私聊发送失败：{type(e).__name__}: {e}")
            return

        yield event.plain_result(
            self.message_manager.build_private_msg_result(response, target),
        )

    async def _query_online_count(self) -> str:
        if self.queqiao_api is None or self.message_manager is None:
            return "QueQiao 客户端还没有初始化。"

        try:
            response = await self.queqiao_api.get_status()
        except TimeoutError:
            return "查询在线人数超时，服务端没有返回状态。"
        except Exception as e:
            logger.exception("Failed to query QueQiao status")
            return f"查询在线人数失败：{type(e).__name__}: {e}"

        return self.message_manager.build_online_count_result(response)

    async def terminate(self) -> None:
        """Stop owned tasks and release connections, including already failed tasks."""
        logger.info("Stopping astrbot_plugin_queqiao_lite")
        if self.queqiao_client is not None:
            self.queqiao_client.stop()
        if self.message_manager is not None:
            self.message_manager.stop()
        for task in self._tasks:
            task.cancel()
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for task, result in zip(self._tasks, results, strict=True):
            if isinstance(result, Exception):
                logger.error("QueQiao task %s failed: %s", task.get_name(), result)
        try:
            if self.queqiao_client is not None:
                await self.queqiao_client.disconnect()
        finally:
            self._tasks.clear()
            self.queqiao_client = None
            self.queqiao_api = None
            self.message_manager = None
        logger.info("Stopped astrbot_plugin_queqiao_lite")
