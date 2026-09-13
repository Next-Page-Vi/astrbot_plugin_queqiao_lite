import asyncio
import time
from http import HTTPStatus

import astrbot.api.message_components as comp
from astrbot.api import logger
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

type QueuedEvent = tuple[EventUnion, float]


class MessageManager:
    def __init__(
        self,
        context: Context,
        enabled_sub_types: list[str],
        umo_list: list[str],
        min_merge_window: int,
        max_merge_window: int,
    ) -> None:
        if not 0 <= min_merge_window < max_merge_window:
            raise ValueError("Merge windows must satisfy 0 <= min < max")
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
            if response.code == HTTPStatus.NOT_FOUND:
                return (
                    "服务端不支持 get_status，请使用 QueQiao v0.5.0 / Tool v0.6.8 "
                    "或具备该接口的版本。"
                )
            return f"查询在线人数失败：{response.error_text}"
        if not isinstance(response, GetStatusResponse):
            return "查询在线人数失败：服务端返回的不是 get_status 响应。"

        server_list_ping = response.data.server_list_ping if response.data is not None else None
        if server_list_ping is not None and server_list_ping.available is False:
            reason = server_list_ping.error or server_list_ping.reason or "服务端未提供原因"
            return f"服务器状态探测失败：{reason}"
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
            if target_player is None:
                reason = response.data.message if response.data is not None else None
                return f"私聊发送失败：{reason or '响应缺少目标玩家，无法确认发送成功。'}"
            uuid_text = str(target_player.uuid) if target_player.uuid else None
            target_text = target_player.nickname or uuid_text or target
            return f"已发送给 {target_text}。"
        return f"私聊发送失败：{self._api_error_text(response)}"

    def build_message(self, event: EventUnion) -> str | None:
        if event.sub_type not in self.enabled_sub_types:
            return None
        nickname = event.player.nickname or "有人"
        server_name = event.server_name or "Server"
        match event:
            case PlayerJoinEvent():
                return f"{nickname} 加入 {server_name}。"
            case PlayerQuitEvent():
                return f"{nickname} 退出 {server_name}。"
            case PlayerDeathEvent():
                if event.death.text and event.death.text.strip():
                    return f"[{server_name}] {event.death.text}"
                detail = f"（{event.death.key}）" if event.death.key else "。"
                return f"[{server_name}] {nickname} 死亡{detail}"
            case PlayerAchievementEvent():
                achievement = event.achievement
                translated_text = (
                    achievement.translation.text if achievement.translation is not None else None
                )
                for full_text in (translated_text, achievement.text):
                    if full_text and full_text.strip():
                        return f"[{server_name}] {full_text}"
                title = achievement.display.title if achievement.display is not None else None
                title_text = (title.text or title.key) if title is not None else None
                detail = title_text or achievement.key or "未知成就"
                return f"{nickname} [{server_name}]: 达成 {detail}。"
            case PlayerChatEvent():
                if event.message and event.message.strip():
                    return f"{nickname} [{server_name}]: {event.message}"
            case PlayerCommandEvent():
                if event.command and event.command.strip():
                    return f"{nickname} [{server_name}]: {event.command}"
        return None

    def stack_messages(self, events_queue: list[QueuedEvent]) -> str | None:
        """Combine nonempty notification lines, or return None when all are skipped."""
        parts = []
        for event, _ in events_queue:
            message_part = self.build_message(event)
            if message_part is not None:
                parts.append(message_part)
        return "\n".join(parts) if parts else None

    def add_message(self, event: EventUnion) -> None:
        """Queue enabled events and cancel opposite events with a known shared identity."""
        if (
            not self._running_flag
            or not self.umo_list
            or event.sub_type not in self.enabled_sub_types
        ):
            return
        cancellation_events = {"player_join": "player_quit", "player_quit": "player_join"}
        cancellation_event = cancellation_events.get(event.sub_type)
        if cancellation_event is not None:
            for index in range(len(self.notification_queue) - 1, -1, -1):
                existing_event, _ = self.notification_queue[index]
                if (
                    existing_event.sub_type != cancellation_event
                    or not event.server_name
                    or existing_event.server_name != event.server_name
                ):
                    continue
                previous, current = existing_event.player, event.player
                if previous.uuid is not None and current.uuid is not None:
                    same_player = previous.uuid == current.uuid
                else:
                    same_player = (
                        previous.nickname is not None
                        and current.nickname is not None
                        and previous.nickname == current.nickname
                    )
                if same_player:
                    del self.notification_queue[index]
                    return
        self.notification_queue.append((event, time.monotonic()))

    async def send_message(self, message_text: str | None, umo_list: list[str]) -> None:
        """Attempt each destination once; a failed target does not block other targets."""
        if message_text is None or not message_text.strip() or not umo_list:
            return
        for umo in umo_list:
            try:
                message_chain = MessageChain(chain=[comp.Plain(message_text)])
                if not await self.context.send_message(umo, message_chain):
                    logger.warning("No AstrBot platform matched notification target %s", umo)
            except Exception:
                logger.exception("Failed to send QueQiao notification to %s", umo)

    async def message_manager_loop(self) -> None:
        """Flush when the oldest event expires or the queue has been quiet long enough."""
        try:
            while self._running_flag:
                await asyncio.sleep(1)
                if not self._running_flag or not self.notification_queue:
                    continue
                first_ts = self.notification_queue[0][1]
                last_ts = self.notification_queue[-1][1]
                now = time.monotonic()
                if (
                    now - first_ts >= self.max_merge_window
                    or now - last_ts >= self.min_merge_window
                ):
                    # Detach the batch before awaiting delivery so newly arrived events stay queued.
                    batch, self.notification_queue = self.notification_queue, []
                    await self.send_message(self.stack_messages(batch), self.umo_list)
        finally:
            logger.info("MessageManager loop exited")

    def stop(self) -> None:
        """Stop accepting events and discard undelivered notifications."""
        self._running_flag = False
        self.notification_queue.clear()
