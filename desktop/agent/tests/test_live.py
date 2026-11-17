"""The WebSocket and the subscription hub."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.context import AgentContext
from app.networking import ws as ws_module
from tests.conftest import FakeCollector, pair_device

WS = "/api/v1/ws"


def connect(socket: Any, token: str) -> dict[str, Any]:
    socket.send_json({"type": "auth", "token": token})
    return socket.receive_json()


def receive_until(socket: Any, predicate: Any, limit: int = 50) -> dict[str, Any]:
    for _ in range(limit):
        message = socket.receive_json()
        if predicate(message):
            return message
    raise AssertionError("expected message did not arrive")


def test_hello_reports_capabilities(client: TestClient, token: str) -> None:
    with client.websocket_connect(WS) as socket:
        hello = connect(socket, token)
    assert hello["type"] == "hello" and hello["protocol_version"] == "1.0"
    assert "system.cpu" in hello["capabilities"]["topics"]
    assert hello["capabilities"]["unsupported_topics"] == {"system.gpu": "no_nvidia_gpu"}


@pytest.mark.parametrize(
    "first", [{"type": "auth", "token": "pbx_wrong"}, {"type": "subscribe", "topics": ["system.cpu"]}, []]
)
def test_socket_closes_4401_without_valid_auth(client: TestClient, first: Any) -> None:
    with client.websocket_connect(WS) as socket:
        socket.send_json(first)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 4401


def test_socket_closes_when_auth_never_arrives(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_module, "AUTH_TIMEOUT_S", 0.05)
    with client.websocket_connect(WS) as socket, pytest.raises(WebSocketDisconnect) as closed:
        socket.receive_json()
    assert closed.value.code == 4401


def test_incompatible_major_version_closes_4426(client: TestClient, token: str) -> None:
    with client.websocket_connect(WS) as socket:
        socket.send_json({"type": "auth", "token": token, "protocol_version": "2.0"})
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 4426


def test_subscribe_streams_data_and_rejects_unsupported(client: TestClient, token: str) -> None:
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_json(
            {"type": "subscribe", "id": "1", "topics": [{"topic": "system.cpu"}, "system.gpu", "nope"]}
        )
        ack = receive_until(socket, lambda m: m["type"] == "ack")
        assert ack["id"] == "1"
        assert ack["result"]["subscribed"] == [{"topic": "system.cpu", "interval_ms": 20}]
        assert ack["result"]["rejected"] == [
            {"topic": "system.gpu", "code": "unsupported"},
            {"topic": "nope", "code": "unknown_topic"},
        ]
        samples = [receive_until(socket, lambda m: m["type"] == "data") for _ in range(3)]
    assert all(s["topic"] == "system.cpu" and "ts" in s for s in samples)
    assert [s["payload"]["n"] for s in samples] == sorted(s["payload"]["n"] for s in samples)


def test_collectors_run_only_while_subscribed(
    client: TestClient, token: str, ctx: AgentContext, collectors: list[FakeCollector]
) -> None:
    cpu, memory = collectors[0], collectors[1]
    assert ctx.hub.running_topics == [] and cpu.samples == 0

    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_json({"type": "subscribe", "id": "1", "topics": ["system.cpu"]})
        receive_until(socket, lambda m: m["type"] == "data")
        assert ctx.hub.running_topics == ["system.cpu"]
        assert memory.samples == 0  # nobody asked for it

        socket.send_json({"type": "unsubscribe", "id": "2", "topics": ["system.cpu"]})
        receive_until(socket, lambda m: m["type"] == "ack" and m["id"] == "2")
        assert ctx.hub.running_topics == []
        socket.send_json({"type": "subscribe", "id": "3", "topics": ["system.cpu"]})
        receive_until(socket, lambda m: m["type"] == "data")

    deadline = time.monotonic() + 2
    while ctx.hub.running_topics and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ctx.hub.running_topics == []  # disconnecting stops sampling
    count = cpu.samples
    time.sleep(0.1)
    assert cpu.samples == count


def test_history_is_replayed_to_a_second_subscriber(client: TestClient, ctx: AgentContext) -> None:
    first, second = pair_device(client, ctx, "one")["token"], pair_device(client, ctx, "two")["token"]
    with client.websocket_connect(WS) as a:
        connect(a, first)
        a.send_json({"type": "subscribe", "id": "1", "topics": ["system.cpu"]})
        for _ in range(3):
            receive_until(a, lambda m: m["type"] == "data")
        with client.websocket_connect(WS) as b:
            connect(b, second)
            b.send_json({"type": "subscribe", "id": "1", "topics": ["system.cpu"]})
            history = receive_until(b, lambda m: m.get("history") is True)
    assert isinstance(history["payload"], list) and len(history["payload"]) >= 3


def test_ping_pong_and_bad_messages_do_not_kill_the_socket(client: TestClient, token: str) -> None:
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_text("this is not json")
        assert socket.receive_json()["code"] == "invalid_request"
        socket.send_json({"type": "launch_missiles"})
        assert socket.receive_json()["code"] == "unknown_type"
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}


def test_idle_socket_closes_4408(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_module, "IDLE_TIMEOUT_S", 0.05)
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 4408


def test_revoking_a_device_closes_its_socket(client: TestClient, token: str, auth: dict[str, str]) -> None:
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        assert client.delete("/api/v1/devices/me", headers=auth).status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 4401


def test_new_connection_replaces_the_old_one(client: TestClient, token: str) -> None:
    with client.websocket_connect(WS) as old:
        connect(old, token)
        with client.websocket_connect(WS) as new:
            assert connect(new, token)["type"] == "hello"
            with pytest.raises(WebSocketDisconnect) as closed:
                old.receive_json()
            assert closed.value.code == 4409
            new.send_json({"type": "ping"})
            assert new.receive_json() == {"type": "pong"}


def test_changes_are_announced_on_the_sync_topic(
    client: TestClient, token: str, auth: dict[str, str]
) -> None:
    with client.websocket_connect(WS) as socket:
        connect(socket, token)
        socket.send_json({"type": "subscribe", "id": "1", "topics": ["sync"]})
        receive_until(socket, lambda m: m["type"] == "ack")
        client.post("/api/v1/tasks", json={"title": "write tests"}, headers=auth)
        event = receive_until(socket, lambda m: m["type"] == "event")
    assert event["topic"] == "sync" and event["payload"] == {"what": "tasks"}
