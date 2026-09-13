from uuid import UUID

from astrbot.api import logger
from pydantic import BaseModel, ConfigDict, Field

from .api_handler import QueqiaoApiResponse, parse_api_response
from .types import JsonArray, JsonObject
from .websocket import QueqiaoClient


class NicknameTarget(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)
    nickname: str = Field(min_length=1)


type PlayerTarget = UUID | NicknameTarget


class QueqiaoApi:
    def __init__(self, client: QueqiaoClient) -> None:
        self.client = client

    @staticmethod
    def _plain_message(message: str) -> JsonArray:
        component: JsonObject = {
            "text": message,
            "color": "white",
        }
        return [component]

    @staticmethod
    def _parse_player_target(target: str) -> PlayerTarget:
        try:
            return UUID(target)
        except ValueError:
            return NicknameTarget(nickname=target)

    async def _request(
        self,
        api: str,
        data: JsonObject,
    ) -> QueqiaoApiResponse | None:
        response = await self.client.send_api_request(api, data)
        logger.debug(response)
        return parse_api_response(response)

    async def get_status(self) -> QueqiaoApiResponse | None:
        return await self._request("get_status", {})

    async def broadcast(self, message: str) -> QueqiaoApiResponse | None:
        data: JsonObject = {
            "message": self._plain_message(message),
        }
        return await self._request(
            "broadcast",
            data,
        )

    async def send_private_msg(
        self,
        target: str,
        message: str,
    ) -> QueqiaoApiResponse | None:
        player_target = self._parse_player_target(target)
        data: JsonObject = {
            "uuid": str(player_target) if isinstance(player_target, UUID) else None,
            "nickname": player_target.nickname
            if isinstance(player_target, NicknameTarget)
            else None,
            "message": self._plain_message(message),
        }
        return await self._request(
            "send_private_msg",
            data,
        )
