import asyncio
import time

import astrbot.api.message_components as comp
from astrbot import logger
from astrbot.api.event import MessageChain
from astrbot.api.star import Context

from .api_handler import (
    BroadcastResponse,
    GetStatusResponse,
    QueqiaoApiResponse,
    SendPrivateMsgResponse,
)
from .event_handler import (
    EventUnion,
    PlayerAchievementEvent,
    PlayerChatEvent,
    PlayerCommandEvent,
    PlayerDeathEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
)

type QueuedEvent = tuple[EventUnion, int]


class MessageManager:
    def __init__(
        self,
        context: Context,
        enabled_sub_types: list[str],
        umo_list: list[str],
        min_merge_window: int,
        max_merge_window: int,
    ) -> None:
        self.context = context
        self.enabled_sub_types = enabled_sub_types
        self.umo_list = umo_list
        self.min_merge_window = min_merge_window
        self.max_merge_window = max_merge_window
        self.notification_queue: list[QueuedEvent] = []
        self._running_flag = True

    @staticmethod
    def _api_error_text(response: QueqiaoApiResponse | None) -> str:
        if response is None:
            return "没有收到有效响应"
        return response.error_text

    @staticmethod
    def _format_number(value: float | None) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, int):
            return str(value)
        return str(value) if value is not None else "?"

    def build_online_count_result(self, response: QueqiaoApiResponse | None) -> str:
        if response is None:
            return "查询在线人数失败：没有收到有效响应"
        if not response.is_success:
            return f"查询在线人数失败：{response.error_text}"
        if not isinstance(response, GetStatusResponse):
            return "查询在线人数失败：服务端返回的不是 get_status 响应。"

        server_list_ping = response.data.server_list_ping if response.data is not None else None
        if server_list_ping is None or server_list_ping.players is None:
            return "已查询服务器状态，但响应中没有在线人数信息。"

        online = self._format_number(server_list_ping.players.online)
        max_players = self._format_number(server_list_ping.players.max)
        return f"当前在线 {online}/{max_players}。"

    def build_broadcast_result(self, response: QueqiaoApiResponse | None) -> str:
        if response is not None and response.is_success:
            if isinstance(response, BroadcastResponse):
                return "已发送到服务器。"
            return "广播发送失败：服务端返回的不是 broadcast 响应。"
        return f"广播发送失败：{self._api_error_text(response)}"

    def build_private_msg_result(
        self,
        response: QueqiaoApiResponse | None,
        target: str,
    ) -> str:
        if response is not None and response.is_success:
            if not isinstance(response, SendPrivateMsgResponse):
                return "私聊发送失败：服务端返回的不是 send_private_msg 响应。"
            target_player = response.data.target_player if response.data else None
            target_text = target
            if target_player is not None:
                uuid_text = str(target_player.uuid) if target_player.uuid else None
                target_text = target_player.nickname or uuid_text or target
            return f"已发送给 {target_text}。"
        return f"私聊发送失败：{self._api_error_text(response)}"

    async def build_message(self, event: EventUnion) -> None | str:
        logger.debug(f"\nEvent: {type(event).__name__}; \nData: \n{event.model_dump()}")
        if not self.enabled_sub_types or event.sub_type not in self.enabled_sub_types:
            logger.debug(f"事件类型 {event.sub_type} 未启用，跳过处理")
            return None
        nickname = event.player.nickname or "有人"
        server_name = event.server_name or "Server"
        match event:
            case PlayerJoinEvent():
                return f"{nickname} 加入 {server_name}。"
            case PlayerQuitEvent():
                return f"{nickname} 退出 {server_name}。"
            case PlayerDeathEvent():
                death_text = event.death.text or ""
                return f"{nickname} [{server_name}]: 死了 {death_text}。"
            case PlayerAchievementEvent():
                title = event.achievement.display.title or ""
                return f"{nickname} [{server_name}]: 达成 {title}。"
            case PlayerChatEvent():
                message = event.message or ""
                return f"{nickname} [{server_name}]: {message}。"
            case PlayerCommandEvent():
                command = event.command or ""
                return f"{nickname} [{server_name}]: {command}。"

    async def stack_messages(self, events_queue: list[QueuedEvent]) -> str:
        """将传入的 events_queue 中的消息堆叠成一条消息返回。"""
        stacked_message: str = ""
        for event, _ in events_queue:
            message_part = await self.build_message(event)
            if message_part:
                stacked_message += message_part + "\n"
        return stacked_message.strip()

    async def add_message(self, event: EventUnion) -> None:
        """将新消息添加到通知队列中，记录时间戳，去抖动处理。"""
        cancellation_events: dict[
            str,
            str,
        ] = {  # 定义可互相取消的事件类型，结构为 {新加入的事件类型: 被取消的事件类型}
            "player_join": "player_quit",
            "player_quit": "player_join",
        }
        if event.sub_type in cancellation_events:
            cancellation_event: str = cancellation_events[event.sub_type]
            for existing_event, _ in reversed(self.notification_queue):
                if (
                    existing_event.sub_type == cancellation_event
                    # 可能没有 uuid，使用 nickname 匹配
                    and existing_event.player.nickname == event.player.nickname
                ):
                    nickname = event.player.nickname or ""
                    logger.debug(
                        f"检测到{nickname}抖动事件，移除对应的{cancellation_event}事件。",
                    )
                    self.notification_queue.remove((existing_event, _))
                    return
        self.notification_queue.append((event, int(time.time())))
        logger.debug(f"notification_queue 内容: {self.notification_queue}")
        return

    async def send_message(
        self,
        message_text: str,
        umo_list: list[str],
        *,
        no_ignore: bool = True,
    ) -> None:
        """发送消息到指定的统一消息源"""
        message_chain = MessageChain(chain=[comp.Plain(message_text)])
        for umo in umo_list:
            try:
                logger.debug(f"Comping message for {umo}: {message_chain}")
                if not no_ignore:
                    logger.info("---Message ignored---")
                    continue
                await self.context.send_message(umo, message_chain)
            except Exception as e:
                logger.error(
                    f"Failed to send message to {umo}: {type(e).__name__}: {e}",
                )

    async def message_manager_loop(self) -> None:
        """消息管理主循环，定期检查并发送通知队列中的消息。"""
        logger.debug("Starting MessageManager main loop...")
        try:
            while self._running_flag:
                await asyncio.sleep(1)
                if len(self.notification_queue) == 0:
                    continue
                first_ts = self.notification_queue[0][1]  # 最早的消息时间戳
                last_ts = self.notification_queue[-1][1]  # 最新的消息时间戳

                async def clear_queue_and_send() -> None:
                    """立即拷贝并清空队列，堆叠并发送消息。"""
                    queue_tobesend = self.notification_queue.copy()
                    self.notification_queue.clear()
                    stacked_message = await self.stack_messages(queue_tobesend)
                    await self.send_message(stacked_message, self.umo_list)
                    queue_tobesend.clear()

                if self.min_merge_window == 0 and self.max_merge_window == 0:
                    await clear_queue_and_send()
                    continue
                # 如果最新消息距离最早消息的时间差超过"最大合并窗口"，则发送通知
                if last_ts - first_ts >= self.max_merge_window:
                    await clear_queue_and_send()
                    continue
                # 否则等待直到去抖动时间到达
                if int(time.time()) >= self.notification_queue[-1][1] + self.min_merge_window:
                    await clear_queue_and_send()
                    continue
        except Exception:
            logger.exception("MessageManager 主循环发生异常")
        finally:
            logger.info("MessageManager loop exited gracefully.")

    async def stop(self) -> None:
        """停止消息管理主循环。"""
        self._running_flag = False
