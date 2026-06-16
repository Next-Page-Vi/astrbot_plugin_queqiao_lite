from typing import Annotated, Literal, Protocol
from uuid import UUID

from astrbot.api import logger
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .types import JsonValue


class QueqiaoBaseModelMixin:
    model_config = ConfigDict(extra="ignore")

    @staticmethod
    def normalize_nullish(data: JsonValue) -> JsonValue:
        """递归将空字符串转 None"""
        if isinstance(data, dict):
            return {
                key: QueqiaoBaseModelMixin.normalize_nullish(value) for key, value in data.items()
            }
        if isinstance(data, list):
            return [QueqiaoBaseModelMixin.normalize_nullish(value) for value in data]
        if data == "":
            return None
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_nullish(cls, data: JsonValue) -> JsonValue:
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


class MessageSink(Protocol):
    async def add_message(self, event: EventUnion) -> None: ...


class EventHandler:
    def __init__(self, message: str, message_manager: MessageSink) -> None:
        self.message = message
        self.message_manager = message_manager

    async def process(self) -> None:
        try:
            event = _event_adapter.validate_json(self.message)
        except (ValidationError, ValueError):
            logger.exception("使用 discriminator 解析事件失败")
            return

        await self.message_manager.add_message(event)
