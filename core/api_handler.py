import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from astrbot.api import logger


class QueqiaoApiBaseModelMixin:
    model_config = ConfigDict(extra="ignore")

    @staticmethod
    def normalize_nullish(data: Any) -> Any:
        """递归将空字符串转 None"""
        if isinstance(data, dict):
            return {
                k: QueqiaoApiBaseModelMixin.normalize_nullish(v)
                if v == "" or isinstance(v, (dict, list))
                else v
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [
                QueqiaoApiBaseModelMixin.normalize_nullish(v)
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


class QueqiaoApiResponse(QueqiaoApiBaseModelMixin, BaseModel):
    api: str
    code: int | None = None
    post_type: Literal["response"] = "response"
    status: str | None = None
    message: str | None = None
    echo: str | None = None
    data: Any = None

    @property
    def is_success(self) -> bool:
        return self.code == 200 and (self.status or "").upper() == "SUCCESS"

    @property
    def error_text(self) -> str:
        if self.message:
            return self.message
        return f"code={self.code}, status={self.status}"


class StatusPlayers(QueqiaoApiBaseModelMixin, BaseModel):
    max: int | float | None = None
    online: int | float | None = None


class ServerListPing(QueqiaoApiBaseModelMixin, BaseModel):
    available: bool | None = None
    host: str | None = None
    port: int | None = None
    players: StatusPlayers | None = None


class GetStatusData(QueqiaoApiBaseModelMixin, BaseModel):
    timestamp: int | None = None
    server_type: str | None = None
    server_version: str | None = None
    server_list_ping: ServerListPing | None = None


class GetStatusResponse(QueqiaoApiResponse):
    api: Literal["get_status"] = "get_status"
    data: GetStatusData | None = None


class BroadcastResponse(QueqiaoApiResponse):
    api: Literal["broadcast"] = "broadcast"


class TargetPlayer(QueqiaoApiBaseModelMixin, BaseModel):
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


class SendPrivateMsgData(QueqiaoApiBaseModelMixin, BaseModel):
    target_player: TargetPlayer | None = None
    message: str | None = None


class SendPrivateMsgResponse(QueqiaoApiResponse):
    api: Literal["send_private_msg"] = "send_private_msg"
    data: SendPrivateMsgData | None = None


ApiResponseUnion = Annotated[
    GetStatusResponse | BroadcastResponse | SendPrivateMsgResponse,
    Field(discriminator="api"),
]

_api_response_adapter: TypeAdapter[ApiResponseUnion] = TypeAdapter(ApiResponseUnion)


class ApiHandler:
    def __init__(self, response: Any) -> None:
        self.response = response

    @staticmethod
    def is_api_response_payload(payload: dict[str, Any]) -> bool:
        return payload.get("post_type") == "response"

    @staticmethod
    def _decode_response(response: Any) -> str | None:
        try:
            if response is None:
                return None
            if isinstance(response, (bytes, bytearray)):
                return response.decode("utf-8")
            if isinstance(response, str):
                return response
            return json.dumps(response, ensure_ascii=False)
        except Exception:
            logger.exception("无法解码 QueQiao API 响应")
            return None

    def process(self) -> QueqiaoApiResponse | None:
        text = self._decode_response(self.response)
        if text is None:
            return None

        try:
            return _api_response_adapter.validate_json(text)
        except Exception:
            logger.debug("使用具体 API 响应模型解析失败，回退到通用响应模型")

        try:
            return QueqiaoApiResponse.model_validate_json(text)
        except Exception:
            logger.exception("解析 QueQiao API 响应失败")
            return None
