"""Core LAN endpoints: pairing, agent info, metrics, actions, control deck and layout."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app import PROTOCOL_VERSION, __version__
from app.api.deps import Ctx, CurrentDevice, client_ip
from app.commands.registry import InvalidParams, execute
from app.core.context import AgentContext
from app.core.errors import ApiError
from app.database.models import Button, ButtonGroup, Layout
from app.platform.ops import PlatformError
from app.security.auth import create_device, redeem_pairing_code

router = APIRouter()


class In(BaseModel):
    """Inbound bodies reject unknown fields, so typos and probing fail loudly."""

    model_config = ConfigDict(extra="forbid")


def _panel_build() -> str:
    """Changes whenever a panel file changes, so open panels know to reload after an update."""
    web = Path(__file__).resolve().parent.parent / "web"
    stamp = "".join(f"{f.name}{f.stat().st_mtime_ns}" for f in sorted(web.rglob("*")) if f.is_file())
    return hashlib.sha1(stamp.encode(), usedforsecurity=False).hexdigest()[:10]


PANEL_BUILD = _panel_build()


def agent_info(ctx: AgentContext) -> dict[str, Any]:
    return {
        "name": ctx.config.server.name,
        "agent_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "panel_build": PANEL_BUILD,
    }


def capabilities(ctx: AgentContext) -> dict[str, Any]:
    return {"topics": ctx.hub.topics, "unsupported_topics": ctx.hub.unsupported, "features": ctx.features()}


# ---- health and pairing (no token) ----------------------------------------


@router.get("/health")
async def health(ctx: Ctx) -> dict[str, Any]:
    return {"status": "ok", **agent_info(ctx)}


class PairRequest(In):
    pairing_code: str = Field(min_length=4, max_length=20)
    device_name: str = Field(min_length=1, max_length=60)
    device_model: str = Field(default="", max_length=60)


@router.post("/pair", status_code=201)
async def pair(body: PairRequest, request: Request, ctx: Ctx) -> dict[str, Any]:
    ip = client_ip(request)
    limits = ctx.limits
    if limits.pairing_lockout.is_locked(ip) or not limits.pairing.allow(ip):
        ctx.audit("pair_rate_limited", ok=False, ip=ip)
        raise ApiError(
            429,
            "rate_limited",
            "Too many pairing attempts. Wait and try again.",
            headers={"Retry-After": "60"},
        )
    with ctx.db() as db:
        if not redeem_pairing_code(db, body.pairing_code):
            limits.pairing_lockout.failure(ip)
            ctx.audit("pair_failed", ok=False, ip=ip)
            raise ApiError(410, "pairing_code_expired", "That code is wrong, already used, or expired.")
        device, token = create_device(db, body.device_name, body.device_model)
    limits.pairing_lockout.success(ip)
    ctx.audit("paired", device_id=device.id, ip=ip, detail=device.name)
    return {"device_id": device.id, "token": token, "agent": agent_info(ctx)}


# ---- agent and device ------------------------------------------------------


@router.get("/agent")
async def agent(ctx: Ctx, device: CurrentDevice) -> dict[str, Any]:
    return {
        **agent_info(ctx),
        "capabilities": capabilities(ctx),
        "device": {"id": device.id, "name": device.name},
    }


async def revoke_device(ctx: AgentContext, device_id: str, ip: str | None) -> bool:
    from app.database.models import Device

    with ctx.db() as db:
        device = db.get(Device, device_id)
        if device is None:
            return False
        device.revoked = True
        db.commit()
    ctx.audit("revoked", device_id=device_id, ip=ip)
    connection = ctx.connections.pop(device_id, None)
    if connection is not None:
        await connection.close(4401)
    return True


@router.delete("/devices/me", status_code=204)
async def unpair(request: Request, ctx: Ctx, device: CurrentDevice) -> None:
    await revoke_device(ctx, device.id, client_ip(request))


# ---- system ----------------------------------------------------------------


@router.get("/system/snapshot")
async def snapshot(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    return await ctx.hub.snapshot()


@router.get("/system/processes")
async def processes(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    if "system.processes" not in ctx.hub.topics:
        raise ApiError(501, "unsupported", "The process list is turned off on this PC.")
    return await ctx.hub.sample_once("system.processes")


# ---- actions ---------------------------------------------------------------


@router.get("/actions")
async def list_actions(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    return {"actions": [a.public() for a in ctx.actions.all()]}


class RunRequest(In):
    confirm_token: str | None = Field(default=None, max_length=64)
    params: dict[str, Any] = Field(default={}, max_length=4)


@router.post("/actions/{action_id}/run")
async def run_action(
    action_id: str, request: Request, ctx: Ctx, device: CurrentDevice, body: RunRequest | None = None
) -> dict[str, Any]:
    ip = client_ip(request)
    action = ctx.actions.get(action_id)
    if action is None:
        raise ApiError(404, "not_found", "That action is not on this PC's allowlist.")
    if not ctx.limits.actions.allow(device.id):
        raise ApiError(429, "rate_limited", "Too many actions. Slow down.", headers={"Retry-After": "5"})

    try:
        params = action.check_params(body.params if body else {})
    except InvalidParams as exc:
        raise ApiError(400, "invalid_request", f"{action.name}: {exc}.") from exc

    if action.danger:
        token = body.confirm_token if body else None
        if not token or not ctx.confirmations.consume(token, device.id, action.id):
            raise ApiError(
                409,
                "confirmation_required",
                f"Confirm: {action.name}?",
                {"confirm_token": ctx.confirmations.issue(device.id, action.id), "expires_in_s": 30},
            )
        if not ctx.limits.dangerous.allow(device.id):
            raise ApiError(429, "rate_limited", "Too many confirmed actions.", headers={"Retry-After": "20"})

    try:
        result = await asyncio.to_thread(execute, action, ctx.platform, params)
    except PlatformError as exc:
        ctx.audit("action", ok=False, device_id=device.id, ip=ip, detail=f"{action.id}: {exc.reason}")
        raise ApiError(422, "action_failed", f"{action.name} failed.", {"reason": exc.reason}) from exc
    ctx.audit("action", device_id=device.id, ip=ip, detail=action.id)
    return result


# ---- control deck ----------------------------------------------------------

ICON = r"^[a-z0-9-]{1,40}$"


class GroupIn(In):
    name: str = Field(min_length=1, max_length=40)


class GroupPatch(In):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    order: int | None = Field(default=None, ge=0, le=999)


class ButtonIn(In):
    group_id: str = Field(max_length=32)
    label: str = Field(min_length=1, max_length=40)
    icon: str = Field(default="bolt", pattern=ICON)
    color: str = Field(default="accent", pattern=r"^[a-z]{1,20}$")
    action_id: str = Field(max_length=64)


class ButtonPatch(In):
    group_id: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, min_length=1, max_length=40)
    icon: str | None = Field(default=None, pattern=ICON)
    color: str | None = Field(default=None, pattern=r"^[a-z]{1,20}$")
    action_id: str | None = Field(default=None, max_length=64)
    order: int | None = Field(default=None, ge=0, le=999)


def _button(ctx: AgentContext, b: Button) -> dict[str, Any]:
    action = ctx.actions.get(b.action_id)
    return {
        "id": b.id,
        "group_id": b.group_id,
        "label": b.label,
        "icon": b.icon,
        "color": b.color,
        "order": b.order,
        "action_id": b.action_id,
        # An action removed from actions.toml leaves the button visible but inert.
        "available": action is not None,
        "danger": bool(action.danger) if action else False,
    }


def _require_action(ctx: AgentContext, action_id: str) -> None:
    if ctx.actions.get(action_id) is None:
        raise ApiError(400, "invalid_request", "That action is not on this PC's allowlist.")


def _changed(ctx: AgentContext, what: str) -> None:
    ctx.hub.publish("sync", {"what": what})


@router.get("/deck")
async def get_deck(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        groups = db.scalars(select(ButtonGroup).order_by(ButtonGroup.order, ButtonGroup.name)).all()
        buttons = db.scalars(select(Button).order_by(Button.order, Button.label)).all()
    return {
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "order": g.order,
                "buttons": [_button(ctx, b) for b in buttons if b.group_id == g.id],
            }
            for g in groups
        ]
    }


@router.post("/deck/groups", status_code=201)
async def create_group(body: GroupIn, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        order = len(db.scalars(select(ButtonGroup)).all())
        group = ButtonGroup(id="g_" + secrets.token_hex(6), name=body.name, order=order)
        db.add(group)
        db.commit()
    _changed(ctx, "deck")
    return {"id": group.id, "name": group.name, "order": group.order, "buttons": []}


@router.patch("/deck/groups/{group_id}")
async def update_group(group_id: str, body: GroupPatch, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        group = db.get(ButtonGroup, group_id)
        if group is None:
            raise ApiError(404, "not_found", "Group not found.")
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(group, key, value)
        db.commit()
    _changed(ctx, "deck")
    return {"id": group.id, "name": group.name, "order": group.order}


@router.delete("/deck/groups/{group_id}", status_code=204)
async def delete_group(group_id: str, ctx: Ctx, _: CurrentDevice) -> None:
    with ctx.db() as db:
        group = db.get(ButtonGroup, group_id)
        if group is None:
            raise ApiError(404, "not_found", "Group not found.")
        for button in db.scalars(select(Button).where(Button.group_id == group_id)).all():
            db.delete(button)
        db.delete(group)
        db.commit()
    _changed(ctx, "deck")


@router.post("/deck/buttons", status_code=201)
async def create_button(body: ButtonIn, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    _require_action(ctx, body.action_id)
    with ctx.db() as db:
        if db.get(ButtonGroup, body.group_id) is None:
            raise ApiError(400, "invalid_request", "Group not found.")
        order = len(db.scalars(select(Button).where(Button.group_id == body.group_id)).all())
        button = Button(id="b_" + secrets.token_hex(6), order=order, params={}, **body.model_dump())
        db.add(button)
        db.commit()
    _changed(ctx, "deck")
    return _button(ctx, button)


@router.patch("/deck/buttons/{button_id}")
async def update_button(button_id: str, body: ButtonPatch, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    if body.action_id is not None:
        _require_action(ctx, body.action_id)
    with ctx.db() as db:
        button = db.get(Button, button_id)
        if button is None:
            raise ApiError(404, "not_found", "Button not found.")
        if body.group_id is not None and db.get(ButtonGroup, body.group_id) is None:
            raise ApiError(400, "invalid_request", "Group not found.")
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(button, key, value)
        db.commit()
    _changed(ctx, "deck")
    return _button(ctx, button)


@router.delete("/deck/buttons/{button_id}", status_code=204)
async def delete_button(button_id: str, ctx: Ctx, _: CurrentDevice) -> None:
    with ctx.db() as db:
        button = db.get(Button, button_id)
        if button is None:
            raise ApiError(404, "not_found", "Button not found.")
        db.delete(button)
        db.commit()
    _changed(ctx, "deck")


def seed_default_deck(ctx: AgentContext) -> None:
    """First run only: a usable deck built from actions that exist on this PC."""
    with ctx.db() as db:
        if db.scalars(select(ButtonGroup)).first() is not None:
            return
        plan = [
            ("Media", [
                ("media.prev", "Previous", "prev"), ("media.play_pause", "Play / Pause", "play"),
                ("media.next", "Next", "next"), ("volume.down", "Vol −", "vol-down"),
                ("volume.mute", "Mute", "mute"), ("volume.up", "Vol +", "vol-up"),
            ]),
            ("Apps", [("open_explorer", "Explorer", "folder"), ("open_terminal", "Terminal", "terminal")]),
            ("System", [
                ("system.lock", "Lock", "lock"), ("system.sleep", "Sleep", "moon"),
                ("system.restart", "Restart", "restart"), ("system.shutdown", "Shut down", "power"),
            ]),
        ]  # fmt: skip
        for g_order, (name, buttons) in enumerate(plan):
            usable = [b for b in buttons if ctx.actions.get(b[0]) is not None]
            if not usable:
                continue
            group = ButtonGroup(id="g_" + secrets.token_hex(6), name=name, order=g_order)
            db.add(group)
            for b_order, (action_id, label, icon) in enumerate(usable):
                color = "danger" if ctx.actions.get(action_id).danger else "accent"  # type: ignore[union-attr]
                db.add(Button(id="b_" + secrets.token_hex(6), group_id=group.id, label=label, icon=icon,
                              color=color, order=b_order, action_id=action_id, params={}))  # fmt: skip
        db.commit()


# ---- layout ----------------------------------------------------------------

IDENT = r"^[A-Za-z0-9_.-]{1,40}$"


class Point(In):
    x: int = Field(ge=0, le=3)
    y: int = Field(ge=0, le=200)


class Size(In):
    w: int = Field(ge=1, le=4)
    h: int = Field(ge=1, le=6)


class Widget(In):
    id: str = Field(pattern=IDENT)
    type: str = Field(pattern=IDENT)
    name: str = Field(max_length=40)
    enabled: bool = True
    position: Point
    size: Size
    config: dict[str, Any] = {}


class Page(In):
    id: str = Field(pattern=IDENT)
    name: str = Field(min_length=1, max_length=30)
    widgets: list[Widget] = Field(max_length=40)


class LayoutDoc(In):
    schema_version: int = Field(alias="schema", ge=1, le=1)
    pages: list[Page] = Field(min_length=1, max_length=12)


@router.get("/layout")
async def get_layout(ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    with ctx.db() as db:
        row = db.get(Layout, "default")
    return {"layout": row.doc if row else None}


@router.put("/layout")
async def put_layout(body: LayoutDoc, ctx: Ctx, _: CurrentDevice) -> dict[str, Any]:
    doc = body.model_dump(by_alias=True)
    with ctx.db() as db:
        row = db.get(Layout, "default")
        if row is None:
            db.add(Layout(key="default", doc=doc))
        else:
            row.doc = doc
        db.commit()
    _changed(ctx, "layout")
    return {"layout": doc}
