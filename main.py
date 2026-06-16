import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.event import filter as event_filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .core.api import QueqiaoApi
from .core.message_manager import MessageManager
from .core.types import JsonObject, JsonValue
from .core.websocket import QueqiaoClient

MCTELL_PART_COUNT = 2


def _config_section(value: JsonValue) -> JsonObject:
    if isinstance(value, dict):
        return value
    return {}


def _config_string(section: JsonObject, key: str, default: str = "") -> str:
    value = section.get(key)
    if isinstance(value, str):
        return value
    return default


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


@register(
    "astrbot_plugin_queqiao_lite",
    "nextpage",
    "A simple Queqiao adapter.",
    "1.1.1",
)
class Queqiaolite(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._tasks: list[asyncio.Task[None]] = []
        self.message_manager: MessageManager | None = None
        self.queqiao_api: QueqiaoApi | None = None
        self.queqiao_client: QueqiaoClient | None = None

    async def initialize(self) -> None:
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        # queqiao_server
        queqiao_server = _config_section(self.config.get("queqiao_server", {}))
        server_name = _config_string(queqiao_server, "server_name")
        server_uri = _config_string(queqiao_server, "server_uri")
        access_token = _config_string(queqiao_server, "access_token")

        # connection_policy
        connection_policy = _config_section(self.config.get("connection_policy", {}))
        max_reconnect_attempts = _config_int(
            connection_policy,
            "max_reconnect_attempts",
            5,
        )
        reconnect_interval = _config_int(connection_policy, "reconnect_interval", 60)

        # notification
        notification = _config_section(self.config.get("notification", {}))
        enabled_events = _config_str_list(notification, "enabled_events")
        umo_list = _config_str_list(notification, "umo_list")
        min_merge_window = _config_int(notification, "min_merge_window", 10)
        max_merge_window = _config_int(notification, "max_merge_window", 60)

        # 映射事件列表
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

        # 参数校验
        if min_merge_window < 0 or max_merge_window < 0 or max_merge_window <= min_merge_window:
            logger.warning(
                "参数校验失败，请检查参数，已设置为默认值 "
                "(max_merge_window = 60, min_merge_window = 10)",
            )
            max_merge_window = 60
            min_merge_window = 10

        # 初始化 MessageManager
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

        # 初始化 QueqiaoClient 并启动监听任务
        self.queqiao_client = QueqiaoClient(
            context=self.context,
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
            logger.exception("发送 QueQiao 广播失败")
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
            logger.exception("发送 QueQiao 私聊失败")
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
            logger.exception("查询 QueQiao 状态失败")
            return f"查询在线人数失败：{type(e).__name__}: {e}"

        return self.message_manager.build_online_count_result(response)

    async def terminate(self) -> None:
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("astrbot_plugin_queqiao_lite 正在关闭...")
        if self.queqiao_client:
            self.queqiao_client.stop()
            await self.queqiao_client.disconnect()
            self.queqiao_client = None
        self.queqiao_api = None
        if self.message_manager:
            await self.message_manager.stop()
            self.message_manager = None
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        logger.info("astrbot_plugin_queqiao_lite 已关闭。")
