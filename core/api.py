from uuid import UUID

from astrbot.api import logger

from .api_handler import ApiHandler, QueqiaoApiResponse
from .types import JsonArray, JsonObject
from .websocket import QueqiaoClient


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
    def _parse_player_target(target: str) -> tuple[str | None, str | None]:
        try:
            return str(UUID(target)), None
        except ValueError:
            return None, target

    async def _request(
        self,
        api: str,
        data: JsonObject,
    ) -> QueqiaoApiResponse | None:
        response = await self.client.send_api_request(api, data)
        logger.debug(response)
        return ApiHandler(response).process()

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
        uuid, nickname = self._parse_player_target(target)
        data: JsonObject = {
            "uuid": uuid,
            "nickname": nickname,
            "message": self._plain_message(message),
        }
        return await self._request(
            "send_private_msg",
            data,
        )
