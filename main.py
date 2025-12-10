import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .core.message_manager import MessageManager
from .core.websocket import QueqiaoClient


@register(
    "astrbot_plugin_queqiao_lite", "nextpage", "A simple Queqiao adapter.", "1.0.0"
)
class Queqiaolite(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._tasks: list[asyncio.Task] = []

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

        # queqiao_server
        queqiao_server = self.config.get("queqiao_server", {})
        server_name = queqiao_server.get("server_name", "")
        server_uri = queqiao_server.get("server_uri", "")
        access_token = queqiao_server.get("access_token", "")

        # connection_policy
        connection_policy = self.config.get("connection_policy", {})
        max_reconnect_attempts = connection_policy.get("max_reconnect_attempts", 5)
        reconnect_interval = connection_policy.get("reconnect_interval", 60)

        # notification
        notification = self.config.get("notification", {})
        enabled_events = notification.get("enabled_events", [])
        umo_list = notification.get("umo_list", [])
        min_merge_window = notification.get("min_merge_window", 10)
        max_merge_window = notification.get("max_merge_window", 60)

        # 映射事件列表
        events_map = {
            "玩家加入|PlayerJoinEvent": "player_join",
            "玩家退出|PlayerQuitEvent": "player_quit",
            "玩家死亡|PlayerDeathEvent": "player_death",
            "玩家成就|PlayerAchievementEvent": "player_achievement",
            "玩家聊天|PlayerChatEvent": "player_chat",
            "玩家命令|PlayerCommandEvent": "player_command",
        }
        enabled_sub_types = [events_map[event] for event in enabled_events]
        logger.info(enabled_sub_types)

        # 参数校验
        if (
            min_merge_window < 0
            or max_merge_window < 0
            or max_merge_window <= min_merge_window
        ):
            logger.warning(
                "参数校验失败，请检查参数，已设置为默认值 (max_merge_window = 60, min_merge_window = 10) "
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
        task_event_listener_loop = asyncio.create_task(
            self.queqiao_client.event_listener_loop(),
            name="task_event_listener_loop",
        )
        self._tasks.append(task_event_listener_loop)

    @filter.command("mc")
    async def mc(self, event: AstrMessageEvent):
        """这是一个 hello world 指令"""  # 这是 handler 的描述，将会被解析方便用户了解插件内容。建议填写。
        user_name = event.get_sender_name()
        message_str = event.message_str  # 用户发的纯文本消息字符串
        message_chain = (
            event.get_messages()
        )  # 用户所发的消息的消息链 # from astrbot.api.message_components import *
        logger.info(message_chain)
        yield event.plain_result(
            f"Hello, {user_name}, 你发了 {message_str}!"
        )  # 发送一条纯文本消息

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("astrbot_plugin_queqiao_lite 正在关闭...")
        if self.queqiao_client:
            self.queqiao_client.stop()
            await self.queqiao_client.disconnect()
            self.queqiao_client = None
        if self.message_manager:
            await self.message_manager.stop()
            self.message_manager = None
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("astrbot_plugin_queqiao_lite 已关闭。")
