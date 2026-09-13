from typing import Annotated, Literal
from uuid import UUID

from astrbot.api import logger
from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from .types import JsonValue


class Player(BaseModel):
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

    @field_validator("nickname", "uuid", "address", mode="before")
    @classmethod
    def normalize_identity(cls, value: JsonValue) -> JsonValue:
        """Normalize missing identity fields without rewriting message content."""
        return None if isinstance(value, str) and not value.strip() else value


class PlayerJoinEvent(BaseModel):
    sub_type: Literal["player_join"] = "player_join"
    timestamp: int | None = None
    post_type: Literal["notice"] = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player


class PlayerQuitEvent(BaseModel):
    sub_type: Literal["player_quit"] = "player_quit"
    timestamp: int | None = None
    post_type: Literal["notice"] = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player


class Translate(BaseModel):
    key: str | None = None
    args: list["Translate"] | None = None
    text: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_text(cls, data: JsonValue) -> JsonValue:
        """Convert legacy strings at the boundary, including recursive arguments."""
        if isinstance(data, str):
            return {"text": data}
        return data


class PlayerDeathEvent(BaseModel):
    sub_type: Literal["player_death"] = "player_death"
    timestamp: int | None = None
    post_type: Literal["notice"] = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player
    death: Translate


class Display(BaseModel):
    title: Translate | None = None
    description: Translate | None = None
    frame: str | None = None


class Achievement(BaseModel):
    key: str | None = None
    display: Display | None = None
    text: str | None = None

    translation: Translate | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_documented_alias(cls, data: JsonValue) -> JsonValue:
        """Prefer the released wire field over the alias used in upstream docs."""
        if isinstance(data, dict) and data.get("translation") is None and "translate" in data:
            return {**data, "translation": data["translate"]}
        return data


class PlayerAchievementEvent(BaseModel):
    sub_type: Literal["player_achievement"] = "player_achievement"
    timestamp: int | None = None
    post_type: Literal["notice"] = "notice"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    player: Player
    achievement: Achievement


class PlayerChatEvent(BaseModel):
    sub_type: Literal["player_chat"] = "player_chat"
    timestamp: int | None = None
    post_type: Literal["message"] = "message"
    event_name: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_type: str | None = None
    message_id: str | None = None
    raw_message: str | None = None
    player: Player
    message: str | None = None


class PlayerCommandEvent(BaseModel):
    sub_type: Literal["player_command"] = "player_command"
    timestamp: int | None = None
    post_type: Literal["message"] = "message"
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


def parse_event(payload: JsonValue) -> EventUnion | None:
    """Validate a decoded event and skip unsupported or malformed payloads.

    Args:
        payload: JSON value decoded by the WebSocket listener.

    Returns:
        A supported event, or None when the payload cannot be handled.
    """
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("sub_type"), str)
        and payload["sub_type"]
        not in {
            "player_join",
            "player_quit",
            "player_death",
            "player_achievement",
            "player_chat",
            "player_command",
        }
    ):
        logger.debug("Skipping unsupported QueQiao event: %s", payload["sub_type"])
        return None
    try:
        return _event_adapter.validate_python(payload)
    except ValidationError:
        logger.exception("Failed to parse QueQiao event")
        return None
