import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star import star_map
from astrbot.core.star.star_manager import PluginManager

from .conftest import PLUGIN_ROOT, plugin

api_models = importlib.import_module(f"{PLUGIN_ROOT.name}.core.api_handler")


def test_real_registration_metadata_and_version(monkeypatch):
    assert star_map[plugin.Queqiaolite.__module__].star_cls_type is plugin.Queqiaolite
    metadata = PluginManager._load_plugin_metadata(str(PLUGIN_ROOT))
    assert metadata.version == "v1.1.2"
    assert metadata.astrbot_version == ">=4.28.0"
    assert PluginManager._validate_astrbot_version_specifier(metadata.astrbot_version)[0]
    manager_module = importlib.import_module("astrbot.core.star.star_manager")
    monkeypatch.setattr(manager_module, "VERSION", "4.27.0")
    assert not PluginManager._validate_astrbot_version_specifier(metadata.astrbot_version)[0]


async def test_real_config_injection_and_reload(context, tmp_path, monkeypatch):
    schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text())
    assert schema["queqiao_server"]["items"]["access_token"]["secret"] is True
    config = AstrBotConfig(config_path=str(tmp_path / "plugin.json"), schema=schema)
    started = asyncio.Event()

    async def listen(_):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(plugin.QueqiaoClient, "event_listener_loop", listen)
    instance = plugin.Queqiaolite(context, config)
    for _ in range(2):
        started.clear()
        await instance.initialize()
        await asyncio.wait_for(started.wait(), timeout=2)
        assert instance.queqiao_client.access_token is None
        assert instance.queqiao_client.server_name == "Server"
        await instance.terminate()
        assert not instance._tasks


@pytest.mark.parametrize(
    ("field", "value"),
    [("server_name", ""), ("server_uri", "https://example.com"), ("server_uri", 123)],
)
async def test_invalid_config_starts_no_tasks(context, config, field, value):
    config["queqiao_server"][field] = value
    instance = plugin.Queqiaolite(context, config)
    with pytest.raises((ValueError, TypeError)):
        await instance.initialize()
    assert not instance._tasks


@pytest.mark.parametrize(
    ("command", "params", "method", "expected"),
    [
        ("mc", [], "get_status", "当前在线 0/20。"),
        ("mc", ["hello", "world"], "broadcast", "已发送到服务器。"),
        ("mctell", ["Steve", "hello", "world"], "send_private_msg", "已发送给 Steve。"),
        (
            "mctell",
            ["11111111-1111-4111-8111-111111111111", "hello"],
            "send_private_msg",
            "已发送给 Steve。",
        ),
        ("mctell", [], None, "用法：/mctell <玩家ID或UUID> <聊天内容>"),
    ],
)
async def test_real_command_parameter_parsing(context, config, command, params, method, expected):
    instance = plugin.Queqiaolite(context, config)
    instance.message_manager = plugin.MessageManager(context, [], [], 10, 60)
    instance.queqiao_api = AsyncMock()
    if method is not None:
        response = {
            "api": method,
            "post_type": "response",
            "code": 200,
            "status": "SUCCESS",
        }
        if method == "get_status":
            response["data"] = {"server_list_ping": {"players": {"online": 0, "max": 20}}}
        elif method == "send_private_msg":
            response["data"] = {"target_player": {"nickname": "Steve"}}
        getattr(instance.queqiao_api, method).return_value = api_models.parse_api_response(response)
    handler = getattr(plugin.Queqiaolite, command)
    parser = CommandFilter(command)
    parser.init_handler_md(SimpleNamespace(handler=handler))
    parsed = parser.validate_and_convert_params(params, parser.handler_params)
    event = SimpleNamespace(plain_result=lambda text: AstrMessageEvent.plain_result(None, text))
    results = [result async for result in getattr(instance, command)(event, **parsed)]
    assert results[0].chain[0].text == expected
    if method == "broadcast":
        instance.queqiao_api.broadcast.assert_awaited_once_with("hello world")
    elif method == "send_private_msg":
        instance.queqiao_api.send_private_msg.assert_awaited_once_with(
            params[0], " ".join(params[1:])
        )


@pytest.mark.parametrize(("minimum", "maximum"), [(-1, 60), (0, 0), (60, 10)])
async def test_invalid_windows_fall_back(context, config, monkeypatch, minimum, maximum):
    config["notification"]["min_merge_window"] = minimum
    config["notification"]["max_merge_window"] = maximum
    monkeypatch.setattr(plugin.QueqiaoClient, "event_listener_loop", AsyncMock())
    instance = plugin.Queqiaolite(context, config)
    await instance.initialize()
    try:
        assert instance.message_manager.min_merge_window == 10
        assert instance.message_manager.max_merge_window == 60
    finally:
        await instance.terminate()
