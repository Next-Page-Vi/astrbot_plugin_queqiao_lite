from typing import Annotated, Literal

from astrbot.api import logger
from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
)

from .event_handler import Player
from .types import JsonValue

SUCCESS_CODE = 200


class QueqiaoApiResponse(BaseModel):
    api: str
    code: int | None = None
    post_type: Literal["response"] = "response"
    status: str | None = None
    message: str | None = None
    echo: str | None = None
    data: JsonValue = None

    @property
    def is_success(self) -> bool:
        return (
            self.code == SUCCESS_CODE
            and self.status is not None
            and self.status.upper() == "SUCCESS"
        )

    @property
    def error_text(self) -> str:
        if self.message:
            return self.message
        return f"code={self.code}, status={self.status}"


class StatusPlayers(BaseModel):
    max: int | float | None = None
    online: int | float | None = None


class ServerListPing(BaseModel):
    available: bool | None = None
    host: str | None = None
    port: int | None = None
    players: StatusPlayers | None = None
    reason: str | None = None
    error: str | None = None


class GetStatusData(BaseModel):
    timestamp: int | None = None
    server_type: str | None = None
    server_version: str | None = None
    server_list_ping: ServerListPing | None = None


class GetStatusResponse(QueqiaoApiResponse):
    api: Literal["get_status"] = "get_status"
    data: GetStatusData | None = None


class BroadcastResponse(QueqiaoApiResponse):
    api: Literal["broadcast"] = "broadcast"


class SendPrivateMsgData(BaseModel):
    target_player: Player | None = None
    message: str | None = None


class SendPrivateMsgResponse(QueqiaoApiResponse):
    api: Literal["send_private_msg"] = "send_private_msg"
    data: SendPrivateMsgData | None = None


ApiResponseUnion = Annotated[
    GetStatusResponse | BroadcastResponse | SendPrivateMsgResponse,
    Field(discriminator="api"),
]

_api_response_adapter: TypeAdapter[ApiResponseUnion] = TypeAdapter(ApiResponseUnion)


def parse_api_response(payload: JsonValue) -> QueqiaoApiResponse | None:
    """Validate a decoded response, preserving generic error details on fallback.

    Args:
        payload: JSON value decoded by the WebSocket listener.

    Returns:
        A typed or generic response, or None when validation fails.
    """
    try:
        return _api_response_adapter.validate_python(payload)
    except ValidationError:
        logger.debug("Falling back to the generic QueQiao response model")

    try:
        return QueqiaoApiResponse.model_validate(payload)
    except ValidationError:
        logger.exception("Failed to parse QueQiao API response")
        return None
