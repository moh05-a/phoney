"""The live WebSocket: authenticate with the first message, then subscribe to topics."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import PROTOCOL_VERSION
from app.api.lan import agent_info, capabilities
from app.core.context import AgentContext
from app.database.models import iso, utcnow
from app.monitoring.hub import Subscriber, UnsupportedTopic
from app.security.auth import authenticate

log = logging.getLogger("phoney.ws")
router = APIRouter()

AUTH_TIMEOUT_S = 5
IDLE_TIMEOUT_S = 45
MAX_TOPICS_PER_MESSAGE = 50


class ClientConnection(Subscriber):
    def __init__(self, ws: WebSocket, device_id: str) -> None:
        super().__init__()
        self.ws = ws
        self.device_id = device_id

    async def close(self, code: int) -> None:
        with contextlib.suppress(RuntimeError):  # already closed by the peer
            await self.ws.close(code)


def _topic_names(raw: Any) -> list[str]:
    """Accepts ["a", "b"] or [{"topic": "a"}]; anything else is ignored."""
    if not isinstance(raw, list):
        return []
    names = []
    for item in raw[:MAX_TOPICS_PER_MESSAGE]:
        name = item.get("topic") if isinstance(item, dict) else item
        if isinstance(name, str) and len(name) <= 64:
            names.append(name)
    return names


def _handle(ctx: AgentContext, conn: ClientConnection, message: Any) -> None:
    if not isinstance(message, dict):
        conn.push({"type": "error", "code": "invalid_request", "message": "Messages must be JSON objects."})
        return
    kind, request_id = message.get("type"), message.get("id")
    if kind == "ping":
        conn.push({"type": "pong"})
    elif kind == "subscribe":
        subscribed, rejected = [], []
        for topic in _topic_names(message.get("topics")):
            try:
                interval = ctx.hub.subscribe(conn, topic)
                subscribed.append({"topic": topic, "interval_ms": interval})
            except UnsupportedTopic as exc:
                rejected.append({"topic": topic, "code": exc.code})
        conn.push(
            {"type": "ack", "id": request_id, "result": {"subscribed": subscribed, "rejected": rejected}}
        )
    elif kind == "unsubscribe":
        names = _topic_names(message.get("topics"))
        for topic in names:
            ctx.hub.unsubscribe(conn, topic)
        conn.push({"type": "ack", "id": request_id, "result": {"unsubscribed": names}})
    else:
        conn.push(
            {"type": "error", "id": request_id, "code": "unknown_type", "message": "Unknown message type."}
        )


async def _writer(conn: ClientConnection) -> None:
    while True:
        await conn.ws.send_json(await conn.queue.get())


@router.websocket("/ws")
async def live(ws: WebSocket) -> None:
    ctx: AgentContext = ws.app.state.ctx
    ip = ws.client.host if ws.client else None
    await ws.accept()

    try:
        first = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_S)
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        with contextlib.suppress(RuntimeError):
            await ws.close(4401)
        return
    token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
    with ctx.db() as db:
        device = authenticate(db, token if isinstance(token, str) else None)
    if device is None:
        ctx.audit("ws_rejected", ok=False, ip=ip)
        await ws.close(4401)
        return
    wanted = str(first.get("protocol_version", PROTOCOL_VERSION))
    if wanted.split(".")[0] != PROTOCOL_VERSION.split(".")[0]:
        await ws.close(4426)
        return

    conn = ClientConnection(ws, device.id)
    previous = ctx.connections.pop(device.id, None)
    if previous is not None:
        await previous.close(4409)
    ctx.connections[device.id] = conn
    ctx.audit("ws_connected", device_id=device.id, ip=ip)

    conn.push(
        {"type": "hello", **agent_info(ctx), "capabilities": capabilities(ctx), "server_time": iso(utcnow())}
    )
    writer = asyncio.create_task(_writer(conn), name=f"ws-writer:{device.id}")
    try:
        while True:
            try:
                message = await asyncio.wait_for(ws.receive_json(), IDLE_TIMEOUT_S)
            except TimeoutError:
                await conn.close(4408)
                break
            except ValueError:
                conn.push({"type": "error", "code": "invalid_request", "message": "Not valid JSON."})
                continue
            _handle(ctx, conn, message)
    except (WebSocketDisconnect, RuntimeError, KeyError):
        pass  # the phone went away, or the agent closed the socket (revocation, replacement)
    finally:
        ctx.hub.remove(conn)
        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await writer
        if ctx.connections.get(device.id) is conn:
            del ctx.connections[device.id]
        ctx.audit("ws_disconnected", device_id=device.id, ip=ip)
