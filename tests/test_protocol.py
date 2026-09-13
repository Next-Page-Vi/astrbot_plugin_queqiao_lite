import importlib
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from .conftest import PLUGIN_ROOT

api_models = importlib.import_module(f"{PLUGIN_ROOT.name}.core.api_handler")
events = importlib.import_module(f"{PLUGIN_ROOT.name}.core.event_handler")
api_module = importlib.import_module(f"{PLUGIN_ROOT.name}.core.api")
manager_module = importlib.import_module(f"{PLUGIN_ROOT.name}.core.message_manager")


@pytest.fixture
def manager(context):
    return manager_module.MessageManager(
        context,
        ["player_chat", "player_death", "player_achievement"],
        ["target"],
        10,
        60,
    )


@pytest.mark.parametrize("field", ["translation", "translate"])
def test_modern_achievement_and_alias(manager, field):
    event = events.parse_event(
        {
            "sub_type": "player_achievement",
            "server_name": "Server",
            "player": {"nickname": "Steve"},
            "achievement": {
                "key": "minecraft:story/mine_stone",
                "display": {
                    "title": {"text": "Stone Age"},
                    "description": {"key": "description", "args": [{"text": "stone"}]},
                },
                field: {"text": "Steve has made the advancement [Stone Age]"},
            },
        }
    )
    assert manager.build_message(event) == "[Server] Steve has made the advancement [Stone Age]"
    assert isinstance(event.achievement.display.title, events.Translate)


def test_translation_priority_and_empty_fallback(manager):
    achievement = events.Achievement.model_validate(
        {
            "translation": {"text": "released"},
            "translate": {"text": "documented"},
        }
    )
    assert achievement.translation.text == "released"
    assert (
        events.Achievement.model_validate(
            {
                "translation": None,
                "translate": {"text": "documented"},
            }
        ).translation.text
        == "documented"
    )


@pytest.mark.parametrize(
    ("achievement", "expected"),
    [
        ({"text": "Steve earned an achievement"}, "[Server] Steve earned an achievement"),
        ({"display": {"title": "Legacy title"}}, "Steve [Server]: 达成 Legacy title。"),
        (
            {"key": "minecraft:story/mine_stone"},
            "Steve [Server]: 达成 minecraft:story/mine_stone。",
        ),
        ({"key": "key", "display": None}, "Steve [Server]: 达成 key。"),
        ({"key": "key", "display": {}}, "Steve [Server]: 达成 key。"),
        ({}, "Steve [Server]: 达成 未知成就。"),
    ],
)
def test_sparse_and_legacy_achievement(manager, achievement, expected):
    event = events.PlayerAchievementEvent(
        player=events.Player(nickname="Steve"),
        achievement=events.Achievement.model_validate(achievement),
    )
    assert manager.build_message(event) == expected


@pytest.mark.parametrize(
    "args",
    [
        ["Steve", "Alex"],
        [{"text": "Steve"}, {"key": "entity.zombie", "args": [{"text": "nested"}]}],
    ],
)
def test_recursive_death_and_legacy_args(manager, args):
    event = events.parse_event(
        {
            "sub_type": "player_death",
            "player": {"nickname": "Steve"},
            "death": {
                "key": "death.attack.player",
                "args": args,
                "text": "Steve was slain by Alex",
            },
        }
    )
    assert all(isinstance(arg, events.Translate) for arg in event.death.args)
    assert manager.build_message(event) == "[Server] Steve was slain by Alex"
    event.death.text = None
    assert manager.build_message(event) == "[Server] Steve 死亡（death.attack.player）"


def test_missing_identity_preserves_values_and_text():
    player = events.Player(nickname=" ", uuid="", health=0, is_op=False)
    assert player.nickname is None
    assert player.uuid is None
    assert player.health == 0
    assert player.is_op is False
    translation = events.Translate.model_validate({"text": "", "args": [""]})
    assert translation.text == ""
    assert translation.args[0].text == ""
    with pytest.raises(ValidationError):
        events.PlayerChatEvent(player=player, post_type="notice")


@pytest.mark.parametrize(
    "payload",
    [
        {"sub_type": "future_event"},
        {"sub_type": "player_chat", "player": None},
        {"sub_type": []},
        {"sub_type": {}},
        {"sub_type": None},
        {"sub_type": 42},
        {"sub_type": True},
        {},
        [],
        None,
        "not an event object",
    ],
)
def test_unknown_and_malformed_events_do_not_escape(payload):
    assert events.parse_event(payload) is None


@pytest.mark.parametrize(
    ("api", "data"),
    [("future_api", None), ("get_status", "unavailable"), ("send_private_msg", [])],
)
def test_generic_response_preserves_error_details(api, data):
    response = api_models.parse_api_response(
        {
            "api": api,
            "post_type": "response",
            "code": 400,
            "status": "FAILED",
            "message": "Request rejected",
            "data": data,
            "future_field": {"value": True},
        }
    )
    assert type(response) is api_models.QueqiaoApiResponse
    assert response.api == api
    assert response.data == data
    assert not response.is_success
    assert response.error_text == "Request rejected"


@pytest.mark.parametrize("payload", [None, [], {}, {"api": []}, {"api": "broadcast", "code": []}])
def test_malformed_api_response_returns_none(payload):
    assert api_models.parse_api_response(payload) is None


@pytest.mark.parametrize(
    "reason",
    [
        "Target player not found.",
        "Target player is not online.",
        "该接口不可用",
    ],
)
def test_outer_success_is_not_private_delivery_success(manager, reason):
    # QueQiaoTool v0.6.8 HandleProtocolMessage wraps these failures in Response.success().
    response = api_models.parse_api_response(
        {
            "api": "send_private_msg",
            "code": 200,
            "status": "SUCCESS",
            "post_type": "response",
            "data": {"target_player": None, "message": reason},
            "echo": "request",
        }
    )
    assert manager.build_private_msg_result(response, "Steve") == f"私聊发送失败：{reason}"


def test_private_success_and_incomplete_response(manager):
    response = api_models.SendPrivateMsgResponse(
        code=200,
        status="SUCCESS",
        data=api_models.SendPrivateMsgData(target_player=events.Player(nickname="Steve")),
    )
    assert manager.build_private_msg_result(response, "original") == "已发送给 Steve。"
    response.data = None
    assert "无法确认" in manager.build_private_msg_result(response, "Steve")


@pytest.mark.parametrize(
    ("ping", "expected"),
    [
        ({"available": True, "players": {"online": 0.0, "max": 20.0}}, "当前在线 0/20。"),
        ({"available": True, "players": {"max": 20}}, "当前在线 ?/20。"),
        (
            {"available": False, "error": "Connection refused"},
            "服务器状态探测失败：Connection refused",
        ),
        ({"available": False, "reason": "disabled"}, "服务器状态探测失败：disabled"),
        ({}, "已查询服务器状态，但响应中没有在线人数信息。"),
    ],
)
def test_status_results(manager, ping, expected):
    response = api_models.parse_api_response(
        {
            "api": "get_status",
            "code": 200,
            "status": "SUCCESS",
            "post_type": "response",
            "data": {"server_list_ping": ping},
        }
    )
    assert manager.build_online_count_result(response) == expected


def test_unsupported_status(manager):
    response = api_models.GetStatusResponse(code=404, status="FAILED")
    assert "QueQiao v0.5.0 / Tool v0.6.8" in manager.build_online_count_result(response)


@pytest.mark.parametrize("target", ["Steve", "11111111-1111-4111-8111-111111111111"])
async def test_private_request_wire_format(target):
    client = AsyncMock()
    client.send_api_request.return_value = {"api": "send_private_msg", "code": 400}
    api = api_module.QueqiaoApi(client)
    await api.send_private_msg(target, "hello world")
    name, data = client.send_api_request.call_args.args
    assert name == "send_private_msg"
    assert (data["uuid"] is None) != (data["nickname"] is None)
    assert data["message"] == [{"text": "hello world", "color": "white"}]
    assert isinstance(api._parse_player_target(target), (UUID, api_module.NicknameTarget))
    with pytest.raises(ValidationError):
        api._parse_player_target(" ")
