import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from urllib.parse import unquote

import pytest
from websockets.asyncio.server import serve

from .conftest import PLUGIN_ROOT, plugin

module = importlib.import_module(f"{PLUGIN_ROOT.name}.core.websocket")


@asynccontextmanager
async def server_endpoint(handler):
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}/custom/path"


@pytest.mark.parametrize("name", ["Server", "中文 + 100%", "Survival+Lobby"])
@pytest.mark.parametrize("token", [None, "test-token"])
async def test_headers_and_concurrent_echo(name, token):
    headers = []

    async def handler(socket):
        headers.append(socket.request.headers)
        requests = [json.loads(await socket.recv()), json.loads(await socket.recv())]
        for request in reversed(requests):
            await socket.send(
                json.dumps(
                    {
                        "api": request["api"],
                        "post_type": "response",
                        "code": 200,
                        "status": "SUCCESS",
                        "echo": request["echo"],
                    }
                )
            )
        await socket.wait_closed()

    async with server_endpoint(handler) as uri:
        client = module.QueqiaoClient(name, uri, access_token=token)
        assert await client._connect()
        listener = asyncio.create_task(client.event_listener_loop())
        try:
            responses = await asyncio.gather(
                client.send_api_request("get_status"),
                client.send_api_request("broadcast"),
            )
            assert [response["api"] for response in responses] == ["get_status", "broadcast"]
            assert unquote(headers[0]["x-self-name"]) == name
            assert headers[0]["x-client-origin"] != "minecraft"
            assert headers[0].get("Authorization") == (f"Bearer {token}" if token else None)
            assert not client._pending_api_requests
        finally:
            client.stop()
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        assert client.websocket is None


@pytest.mark.parametrize(
    "raw_event",
    [
        '{"sub_type":[]}',
        '{"sub_type":{}}',
        '{"sub_type":null}',
        '{"sub_type":42}',
        '{"sub_type":true}',
        '{"sub_type":"future_event"}',
        '{"sub_type":"player_chat","player":null}',
        "invalid JSON",
        "[]",
        "null",
        b"\xff",
    ],
)
async def test_malformed_event_does_not_stop_listener(raw_event):
    incoming = asyncio.Queue()
    sink = Mock()
    socket = AsyncMock()
    socket.recv.side_effect = incoming.get
    client = module.QueqiaoClient("Server", "ws://localhost:8080", message_manager=sink)
    client.websocket = socket

    async def respond(raw):
        request = json.loads(raw)
        await incoming.put(raw_event)
        await incoming.put(
            json.dumps(
                {
                    "sub_type": "player_chat",
                    "player": {"nickname": "Steve", "future_player_field": True},
                    "message": "still listening",
                    "future_event_field": {"value": True},
                }
            ).encode("utf-8")
        )
        await incoming.put(
            json.dumps(
                {
                    "api": request["api"],
                    "post_type": "response",
                    "code": 200,
                    "status": "SUCCESS",
                    "echo": request["echo"],
                }
            )
        )

    socket.send.side_effect = respond
    listener = asyncio.create_task(client.event_listener_loop())
    try:
        response = await client.send_api_request("get_status", response_timeout=2)
        assert response["api"] == "get_status"
        sink.add_message.assert_called_once()
        event = sink.add_message.call_args.args[0]
        assert event.sub_type == "player_chat"
        assert event.message == "still listening"
        assert client._running_flag
        assert not listener.done()
        assert client.websocket is socket
        socket.close.assert_not_awaited()
        assert not client._pending_api_requests
    finally:
        client.stop()
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)


@pytest.mark.parametrize("reject_after_request", [False, True])
async def test_policy_rejection_stops_retries(reject_after_request):
    connections = 0
    ready = asyncio.Event()

    async def handler(socket):
        nonlocal connections
        connections += 1
        ready.set()
        if reject_after_request:
            await socket.recv()
        await socket.close(1008, "X-Self-name Header is wrong")

    async with server_endpoint(handler) as uri:
        client = module.QueqiaoClient(
            "Server", uri, max_reconnect_attempts=-1, reconnect_interval=0
        )
        if reject_after_request:
            assert await client._connect()
        task = asyncio.create_task(client.event_listener_loop())
        try:
            await asyncio.wait_for(ready.wait(), timeout=2)
            if reject_after_request:
                with pytest.raises(ConnectionError):
                    await client.send_api_request("get_status")
            with pytest.raises(module.AuthenticationError):
                await asyncio.wait_for(task, timeout=2)
            assert connections == 1
            assert client.websocket is None
            assert not client._pending_api_requests
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_transport_disconnect_reconnects():
    connections = 0
    connected_again = asyncio.Event()

    async def handler(socket):
        nonlocal connections
        connections += 1
        if connections == 1:
            await socket.close(1011, "temporary failure")
        else:
            connected_again.set()
            await socket.wait_closed()

    async with server_endpoint(handler) as uri:
        client = module.QueqiaoClient("Server", uri, reconnect_interval=0)
        task = asyncio.create_task(client.event_listener_loop())
        try:
            await asyncio.wait_for(connected_again.wait(), timeout=2)
            assert connections == 2
        finally:
            client.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert client.websocket is None


async def test_timeout_cancellation_and_uncorrelated_response():
    received = asyncio.Queue()

    async def handler(socket):
        async for raw in socket:
            await received.put(json.loads(raw))
            await socket.send(
                json.dumps({"post_type": "response", "code": 500, "message": "parse failed"})
            )

    async with server_endpoint(handler) as uri:
        client = module.QueqiaoClient("Server", uri)
        assert await client._connect()
        listener = asyncio.create_task(client.event_listener_loop())
        try:
            with pytest.raises(TimeoutError):
                await client.send_api_request("get_status", response_timeout=0.05)
            assert not client._pending_api_requests
            await received.get()
            request = asyncio.create_task(client.send_api_request("get_status"))
            await asyncio.wait_for(received.get(), timeout=2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert not client._pending_api_requests
            request = asyncio.create_task(client.send_api_request("get_status"))
            await asyncio.wait_for(received.get(), timeout=2)
            client.stop()
            await client.disconnect()
            with pytest.raises(ConnectionError):
                await request
            assert not client._pending_api_requests
        finally:
            client.stop()
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)


async def test_terminate_during_connection_probe(context, config, monkeypatch):
    probing = asyncio.Event()
    socket = AsyncMock()

    async def ping():
        probing.set()
        await asyncio.Event().wait()

    socket.ping.side_effect = ping
    monkeypatch.setattr(module.websockets, "connect", AsyncMock(return_value=socket))
    instance = plugin.Queqiaolite(context, config)
    await instance.initialize()
    await asyncio.wait_for(probing.wait(), timeout=2)
    tasks = list(instance._tasks)
    await asyncio.wait_for(instance.terminate(), timeout=2)
    socket.close.assert_awaited_once()
    assert all(task.done() for task in tasks)
    assert not instance._tasks
    assert instance.queqiao_client is None
    await instance.terminate()


async def test_terminate_after_task_failure(context, config):
    instance = plugin.Queqiaolite(context, config)

    async def failed():
        raise ConnectionError("exhausted")

    task = asyncio.create_task(failed())
    await asyncio.wait([task])
    instance._tasks.append(task)
    await instance.terminate()
    assert not instance._tasks
    await instance.terminate()


async def test_retry_exhaustion_is_bounded_and_closes(monkeypatch):
    client = module.QueqiaoClient(
        "Server", "ws://127.0.0.1:1", max_reconnect_attempts=2, reconnect_interval=0
    )
    connect = AsyncMock(side_effect=OSError("refused"))
    monkeypatch.setattr(module.websockets, "connect", connect)
    await client.event_listener_loop()
    assert connect.await_count == 3
    assert client.websocket is None
    assert not client._running_flag


async def test_cancellation_before_connection_exists(context, config, monkeypatch):
    connecting = asyncio.Event()

    async def connect(*_args, **_kwargs):
        connecting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(module.websockets, "connect", connect)
    instance = plugin.Queqiaolite(context, config)
    await instance.initialize()
    await asyncio.wait_for(connecting.wait(), timeout=2)
    tasks = list(instance._tasks)
    await asyncio.wait_for(instance.terminate(), timeout=2)
    assert all(task.done() for task in tasks)
    assert instance.queqiao_client is None


async def test_send_failure_and_cancellation_release_pending():
    client = module.QueqiaoClient("Server", "ws://localhost:8080")
    socket = AsyncMock()
    client.websocket = socket
    socket.send.side_effect = OSError("send failed")
    with pytest.raises(OSError, match="send failed"):
        await client.send_api_request("broadcast")
    assert not client._pending_api_requests
    sending = asyncio.Event()

    async def send(_):
        sending.set()
        await asyncio.Event().wait()

    socket.send.side_effect = send
    request = asyncio.create_task(client.send_api_request("broadcast"))
    await asyncio.wait_for(sending.wait(), timeout=2)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not client._pending_api_requests
    await client.disconnect()


async def test_cancellation_while_waiting_to_send_releases_request():
    client = module.QueqiaoClient("Server", "ws://localhost:8080")
    socket = AsyncMock()
    client.websocket = socket
    sending = asyncio.Event()
    second_started = asyncio.Event()

    async def send(_):
        sending.set()
        await asyncio.Event().wait()

    async def second_request():
        second_started.set()
        return await client.send_api_request("get_status")

    socket.send.side_effect = send
    first = asyncio.create_task(client.send_api_request("broadcast"))
    second = asyncio.create_task(second_request())
    try:
        await asyncio.wait_for(sending.wait(), timeout=2)
        await asyncio.wait_for(second_started.wait(), timeout=2)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert len(client._pending_api_requests) == 1
        socket.send.assert_awaited_once()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not client._pending_api_requests
    finally:
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        await client.disconnect()


async def test_request_without_connection_leaves_no_pending_requests():
    client = module.QueqiaoClient("Server", "ws://localhost:8080")
    with pytest.raises(ConnectionError, match="not connected"):
        await client.send_api_request("get_status")
    assert not client._pending_api_requests
