"""Request plumbing shared by the routers: context lookup, authentication, source checks."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.context import AgentContext
from app.core.errors import ApiError
from app.database.models import Device, utcnow
from app.security.auth import authenticate, bearer_token, is_private_address

MAX_BODY_BYTES = 64 * 1024


def get_ctx(conn: HTTPConnection) -> AgentContext:
    return conn.app.state.ctx


def client_ip(conn: HTTPConnection) -> str:
    return conn.client.host if conn.client else "unknown"


def current_device(conn: HTTPConnection, ctx: Annotated[AgentContext, Depends(get_ctx)]) -> Device:
    token = bearer_token(conn.headers.get("authorization"))
    with ctx.db() as db:
        device = authenticate(db, token)
        if device is None:
            raise ApiError(401, "unauthorized", "This device is not paired, or its access was revoked.")
        if not ctx.limits.requests.allow(device.id):
            raise ApiError(429, "rate_limited", "Too many requests.", headers={"Retry-After": "5"})
        now = utcnow()
        last = device.last_seen_at
        if last is None or now - last.replace(tzinfo=now.tzinfo) > timedelta(minutes=1):
            device.last_seen_at = now
            db.commit()
    return device


Ctx = Annotated[AgentContext, Depends(get_ctx)]
CurrentDevice = Annotated[Device, Depends(current_device)]


class GuardMiddleware:
    """Runs before routing, so it also covers static files and WebSocket upgrades.

    Refuses clients outside private address ranges and request bodies larger than the protocol allows.
    """

    def __init__(self, app: ASGIApp, ctx: AgentContext) -> None:
        self.app = app
        self.ctx = ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        host = client[0] if client else None
        if self.ctx.config.security.enforce_private_sources and not is_private_address(host):
            self.ctx.audit("source_rejected", ok=False, ip=host)
            await self._refuse(
                scope, send, 403, "forbidden", "Only devices on the local network may connect."
            )
            return
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            length = headers.get(b"content-length", b"0")
            if length.isdigit() and int(length) > MAX_BODY_BYTES:
                await self._refuse(scope, send, 413, "invalid_request", "Request body too large.")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, send: Send, status: int, code: str, message: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        body = json.dumps({"error": {"code": code, "message": message, "details": {}}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
