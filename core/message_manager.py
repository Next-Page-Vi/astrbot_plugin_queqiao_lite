import asyncio
import time

import astrbot.api.message_components as Comp
from astrbot import logger
from astrbot.api.event import MessageChain

from .message_handler import (
    EventUnion,
    PlayerAchievementEvent,
    PlayerChatEvent,
    PlayerCommandEvent,
    PlayerDeathEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
)


class MessageManager:
    def __init__(
        self, context, enabled_sub_types, umo_list, min_merge_window, max_merge_window
    ) -> None:
        self.context = context
        self.enabled_sub_types = enabled_sub_types
        self.umo_list = umo_list
        self.min_merge_window = min_merge_window
        self.max_merge_window = max_merge_window
        self.notification_queue: list[tuple[EventUnion, int]] = []
        self._running_flag = True

    async def build_message(self, event: EventUnion) -> None | str:
        logger.debug(f"\nEvent: {type(event).__name__}; \nData: \n{event.model_dump()}")
        if not self.enabled_sub_types or event.sub_type not in self.enabled_sub_types:
            logger.debug(f"事件类型 {event.sub_type} 未启用，跳过处理")
            return None
        match event:
            case PlayerJoinEvent():
                return f"{getattr(event.player, 'nickname', '有人')} 加入了 {getattr(event, 'server_name', '服务器')}。"
            case PlayerQuitEvent():
                return f"{getattr(event.player, 'nickname', '有人')} 退出了 {getattr(event, 'server_name', '服务器')}。"
            case PlayerDeathEvent():
                return f"{getattr(event.player, 'nickname', '有人')} 在 {getattr(event, 'server_name', '服务器')} 死了 {getattr(event.death, 'text', '')}。"
            case PlayerAchievementEvent():
                return f"{getattr(event.player, 'nickname', '有人')} 在 {getattr(event, 'server_name', '服务器')} 达成成就 {getattr(event.achievement.display, 'title', '')}。"
            case PlayerChatEvent():
                return f"{getattr(event.player, 'nickname', '有人')} 在 {getattr(event, 'server_name', '服务器')} 说: {getattr(event, 'message', '')}。"
            case PlayerCommandEvent():
                return f"{getattr(event.player, 'nickname', '有人')} 在 {getattr(event, 'server_name', '服务器')} 执行: {getattr(event, 'command', '')}。"

    async def stack_messages(self, events_queue) -> str:
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
            str, str
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
                    logger.debug(
                        f"检测到{getattr(event.player, 'nickname', '')}抖动事件，移除对应的{cancellation_event}事件。"
                    )
                    self.notification_queue.remove((existing_event, _))
                    return
        self.notification_queue.append((event, int(time.time())))
        logger.debug(f"notification_queue 内容: {self.notification_queue}")
        return

    async def send_message(
        self, message_text: str, umo_list: list[str], no_ignore: bool = True
    ) -> None:
        """发送消息到指定的统一消息源"""
        message_chain = MessageChain(chain=[Comp.Plain(message_text)])
        for umo in umo_list:
            try:
                logger.debug(f"Comping message for {umo}: {message_chain}")
                if not no_ignore:
                    logger.info("---Message ignored---")
                    continue
                await self.context.send_message(umo, message_chain)
            except Exception as e:
                logger.error(
                    f"Failed to send message to {umo}: {type(e).__name__}: {e}"
                )

    async def message_manager_loop(self):
        """消息管理主循环，定期检查并发送通知队列中的消息。"""
        logger.debug("Starting MessageManager main loop...")
        try:
            while self._running_flag:
                await asyncio.sleep(1)
                if len(self.notification_queue) == 0:
                    continue
                first_ts = self.notification_queue[0][1]  # 最早的消息时间戳
                last_ts = self.notification_queue[-1][1]  # 最新的消息时间戳

                async def clear_queue_and_send():
                    """立即拷贝并清空队列，堆叠并发送消息。"""
                    queue_tobesend: list = self.notification_queue.copy()
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
                if (
                    int(time.time())
                    >= self.notification_queue[-1][1] + self.min_merge_window
                ):
                    await clear_queue_and_send()
                    continue
        except Exception:
            logger.exception("MessageManager 主循环发生异常")
        finally:
            logger.info("MessageManager loop exited gracefully.")

    async def stop(self):
        """停止消息管理主循环。"""
        self._running_flag = False
