import asyncio
import importlib
from unittest.mock import AsyncMock

import pytest

from .conftest import PLUGIN_ROOT

events = importlib.import_module(f"{PLUGIN_ROOT.name}.core.event_handler")
module = importlib.import_module(f"{PLUGIN_ROOT.name}.core.message_manager")


def make_manager(context, enabled=None, targets=None, minimum=10):
    return module.MessageManager(
        context,
        enabled if enabled is not None else ["player_join", "player_quit", "player_chat"],
        targets if targets is not None else ["target"],
        minimum,
        60,
    )


@pytest.mark.parametrize(("enabled", "targets"), [([], ["target"]), (["player_chat"], [])])
def test_disabled_or_no_targets_never_queue(context, enabled, targets):
    manager = make_manager(context, enabled, targets)
    manager.add_message(
        events.PlayerChatEvent(player=events.Player(nickname="Steve"), message="Hi")
    )
    assert manager.notification_queue == []
    context.send_message.assert_not_called()


@pytest.mark.parametrize(
    ("old_player", "new_player", "old_server", "new_server", "cancels"),
    [
        (
            {"uuid": "11111111-1111-4111-8111-111111111111"},
            {"uuid": "11111111-1111-4111-8111-111111111111"},
            "A",
            "A",
            True,
        ),
        (
            {"uuid": "11111111-1111-4111-8111-111111111111", "nickname": "Steve"},
            {"uuid": "22222222-2222-4222-8222-222222222222", "nickname": "Steve"},
            "A",
            "A",
            False,
        ),
        ({"nickname": "Steve"}, {"nickname": "Steve"}, "A", "A", True),
        ({}, {}, "A", "A", False),
        ({"nickname": "Steve"}, {"nickname": "Steve"}, "A", "B", False),
        ({"nickname": "Steve"}, {"nickname": "Steve"}, None, None, False),
    ],
)
def test_identity_cancellation(context, old_player, new_player, old_server, new_server, cancels):
    manager = make_manager(context)
    manager.add_message(
        events.PlayerJoinEvent(
            player=events.Player.model_validate(old_player),
            server_name=old_server,
        )
    )
    manager.add_message(
        events.PlayerQuitEvent(
            player=events.Player.model_validate(new_player),
            server_name=new_server,
        )
    )
    assert len(manager.notification_queue) == (0 if cancels else 2)


def test_disabled_quit_does_not_cancel_join(context):
    manager = make_manager(context, ["player_join"])
    player = events.Player(nickname="Steve")
    manager.add_message(events.PlayerJoinEvent(player=player, server_name="Server"))
    manager.add_message(events.PlayerQuitEvent(player=player, server_name="Server"))
    assert len(manager.notification_queue) == 1


@pytest.mark.parametrize(
    ("minimum", "now", "should_send"),
    [(10, 60, True), (10, 59, False), (10, 69, True), (0, 59, True)],
)
async def test_monotonic_merge_deadlines(context, monkeypatch, minimum, now, should_send):
    manager = make_manager(context, minimum=minimum)
    event = events.PlayerChatEvent(player=events.Player(nickname="Steve"), message="Hello")
    manager.notification_queue = [(event, 0.0), (event, 59.0)]
    monkeypatch.setattr(module.time, "monotonic", lambda: now)
    # One check per iteration; the next iteration exits without waiting in real time.
    iterations = 0

    async def tick(_):
        nonlocal iterations
        iterations += 1
        if iterations > 1:
            manager._running_flag = False

    monkeypatch.setattr(module.asyncio, "sleep", tick)
    await manager.message_manager_loop()
    assert context.send_message.await_count == int(should_send)
    assert len(manager.notification_queue) == (0 if should_send else 2)


async def test_empty_body_and_target_failures(context):
    manager = make_manager(context)
    empty_event = events.PlayerChatEvent(player=events.Player(nickname="Steve"), message=" ")
    assert manager.stack_messages([(empty_event, 1.0)]) is None
    for empty in [None, "", " "]:
        await manager.send_message(empty, ["target"])
    context.send_message.assert_not_called()
    context.send_message = AsyncMock(side_effect=[False, RuntimeError("delivery failed"), True])
    await manager.send_message("Hello", ["missing", "broken", "working"])
    assert [call.args[0] for call in context.send_message.await_args_list] == [
        "missing",
        "broken",
        "working",
    ]


async def test_new_events_survive_delivery_and_stop_discards(context, monkeypatch):
    manager = make_manager(context, minimum=0)
    event = events.PlayerChatEvent(player=events.Player(nickname="Steve"), message="Hello")
    manager.notification_queue = [(event, 0.0)]
    delivered = asyncio.Event()
    release = asyncio.Event()

    async def send(*_):
        delivered.set()
        await release.wait()
        return True

    context.send_message.side_effect = send
    task = asyncio.create_task(manager.message_manager_loop())
    try:
        await asyncio.wait_for(delivered.wait(), timeout=2)
        manager.add_message(event)
        assert len(manager.notification_queue) == 1
        manager.stop()
        release.set()
        await asyncio.wait_for(task, timeout=2)
        assert not manager.notification_queue
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
