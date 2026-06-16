import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from websockets import Data

from astrbot.api import logger


class QueqiaoBaseModelMixin:
    model_config = ConfigDict(extra="ignore")

    @staticmethod
    def normalize_nullish(data: Any) -> Any:
        """递归将空字符串转 None"""
        if isinstance(data, dict):
            return {
                k: QueqiaoBaseModelMixin.normalize_nullish(v)
                if v == "" or isinstance(v, (dict, list))
                else v
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [
                QueqiaoBaseModelMixin.normalize_nullish(v)
                if v == "" or isinstance(v, (dict, list))
                else v
                for v in data
            ]
        elif data == "":
            return None
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_nullish(cls, data: Any) -> Any:
        return cls.normalize_nullish(data)


class Player(QueqiaoBaseModelMixin, BaseModel):
    nickname: str | None = None
    uuid: UUID | None = None
    is_op: bool | None = None
    address: str | None = None
    health: float | None = None
    max_health: float | None = None
    experience_level: int | None = None
    experience_progress: float | None = None
    total_experience: int | None = None
    walk_speed: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None


class PlayerJoinEvent(QueqiaoBaseModelMixin, BaseModel):
    sub_type: Literal["player_join"] = "player_join"
    timestamp: int | None = None
    post_type: str = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player


class PlayerQuitEvent(QueqiaoBaseModelMixin, BaseModel):
    sub_type: Literal["player_quit"] = "player_quit"
    timestamp: int | None = None
    post_type: str = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player


class Death(QueqiaoBaseModelMixin, BaseModel):
    key: str | None = None
    args: list[str] | None = None
    text: str | None = None


class PlayerDeathEvent(QueqiaoBaseModelMixin, BaseModel):
    sub_type: Literal["player_death"] = "player_death"
    timestamp: int | None = None
    post_type: str = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player
    death: Death


class Display(QueqiaoBaseModelMixin, BaseModel):
    title: str | None = None
    description: str | None = None
    frame: str | None = None


class Achievement(QueqiaoBaseModelMixin, BaseModel):
    key: str | None = None
    display: Display
    text: str | None = None


class PlayerAchievementEvent(QueqiaoBaseModelMixin, BaseModel):
    sub_type: Literal["player_achievement"] = "player_achievement"
    timestamp: int | None = None
    post_type: str = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player
    achievement: Achievement


class PlayerChatEvent(QueqiaoBaseModelMixin, BaseModel):
    sub_type: Literal["player_chat"] = "player_chat"
    timestamp: int | None = None
    post_type: str = "message"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    message_id: str | None = None
    raw_message: str | None = None
    player: Player
    message: str | None = None


class PlayerCommandEvent(QueqiaoBaseModelMixin, BaseModel):
    sub_type: Literal["player_command"] = "player_command"
    timestamp: int | None = None
    post_type: str = "message"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    message_id: str | None = None
    raw_message: str | None = None
    player: Player
    command: str | None = None


EventUnion = Annotated[
    PlayerJoinEvent
    | PlayerQuitEvent
    | PlayerDeathEvent
    | PlayerAchievementEvent
    | PlayerChatEvent
    | PlayerCommandEvent,
    Field(discriminator="sub_type"),
]

_event_adapter: TypeAdapter[EventUnion] = TypeAdapter(EventUnion)


class EventHandler:
    def __init__(self, context, message: Data, message_manager) -> None:
        self.context = context
        self.message = message
        self.message_manager = message_manager

    async def process(self) -> None:
        try:
            if isinstance(self.message, (bytes, bytearray)):
                text = self.message.decode("utf-8")
            elif isinstance(self.message, str):
                text = self.message
            else:
                text = json.dumps(self.message)
        except Exception:
            logger.exception("无法解码 WebSocket 消息为文本")
            return

        try:
            event = _event_adapter.validate_json(text)
        except Exception:
            logger.exception("使用 discriminator 解析事件失败")
            return

        await self.message_manager.add_message(event)
