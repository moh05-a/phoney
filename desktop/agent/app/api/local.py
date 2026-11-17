"""The PC-only listener: status page, pairing page, device revocation and AI-tool hook intake.

Bound to 127.0.0.1. The Host check below also stops a web page from reaching it through DNS rebinding.
"""

from __future__ import annotations

import html
import io
import socket
from typing import Any

import segno
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.deps import MAX_BODY_BYTES, Ctx
from app.api.lan import revoke_device
from app.core.context import AgentContext
from app.core.errors import ApiError, install_error_handlers
from app.database.models import AuditLog, Device, iso
from app.integrations.ai import TOOLS
from app.integrations.permissions import hook_response
from app.security.auth import create_pairing_code, format_code

router = APIRouter()


def lan_ip() -> str:
    """The address other devices on the network use to reach this PC. Sends no packets."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp, port: int) -> None:
        self.app = app
        self.allowed = {f"127.0.0.1:{port}".encode(), f"localhost:{port}".encode()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            origin = headers.get(b"origin")
            bad_origin = origin is not None and origin.split(b"//")[-1] not in self.allowed
            if headers.get(b"host") not in self.allowed or bad_origin:
                await JSONResponse(
                    {"error": {"code": "forbidden", "message": "Local access only.", "details": {}}}, 403
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Phoney agent</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
:root{{color-scheme:dark}}body{{font:15px/1.5 system-ui,sans-serif;background:#0b0d10;color:#e6e9ee;margin:0;padding:32px;max-width:860px;margin:auto}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#8a94a3;margin:32px 0 8px}}
a{{color:#5cc8ff}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:7px 10px;border-bottom:1px solid #1e232b;font-size:14px}}
th{{color:#8a94a3;font-weight:500}}code,.mono{{font-family:ui-monospace,Consolas,monospace}}
.card{{background:#12151a;border:1px solid #1e232b;border-radius:12px;padding:20px}}.muted{{color:#8a94a3}}
.btn{{display:inline-block;background:#5cc8ff;color:#06131c;border:0;border-radius:8px;padding:9px 16px;font-weight:600;text-decoration:none;cursor:pointer;font-size:14px}}
.btn.ghost{{background:transparent;color:#ff6b6b;border:1px solid #3a2326;padding:4px 10px}}
.code{{font:600 34px ui-monospace,Consolas,monospace;letter-spacing:.12em}}.qr{{background:#fff;padding:12px;border-radius:12px;display:inline-block;line-height:0}}
.row{{display:flex;gap:28px;align-items:center;flex-wrap:wrap}}.bad{{color:#ff6b6b}}ol{{padding-left:20px}}li{{margin:6px 0}}
</style></head><body>{body}</body></html>"""


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(body=body), headers={"Cache-Control": "no-store"})


@router.get("/", response_class=HTMLResponse)
async def status(ctx: Ctx) -> HTMLResponse:
    e = html.escape
    with ctx.db() as db:
        devices = db.scalars(
            select(Device).where(Device.revoked.is_(False)).order_by(Device.created_at)
        ).all()
        log = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(25)).all()
    device_rows = (
        "".join(
            f"<tr><td>{e(d.name)}</td><td class=mono>{e(d.id)}</td>"
            f"<td>{'connected' if d.id in ctx.connections else 'offline'}</td><td class=muted>{e(iso(d.last_seen_at) or 'never')}</td>"
            f"<td><form method=post action='/local/devices/{e(d.id)}/revoke'><button class='btn ghost'>Revoke</button></form></td></tr>"
            for d in devices
        )
        or "<tr><td colspan=5 class=muted>No phone paired yet.</td></tr>"
    )
    log_rows = "".join(
        f"<tr><td class='mono muted'>{e(iso(r.ts) or '')}</td><td class='{'' if r.ok else 'bad'}'>{e(r.event)}</td>"
        f"<td class=mono>{e(r.ip or '')}</td><td class=muted>{e(r.detail)}</td></tr>"
        for r in log
    )
    url = f"http://{lan_ip()}:{ctx.config.server.lan_port}/"
    unsupported = ", ".join(f"{t} ({r})" for t, r in ctx.hub.unsupported.items()) or "none"
    return _page(
        f"<h1>Phoney agent · {e(ctx.config.server.name)}</h1>"
        f"<p class=muted>Panel address for the phone: <code>{e(url)}</code> · Data folder: <code>{e(str(ctx.data_dir))}</code></p>"
        f"<p><a class=btn href='/pair'>Pair a phone</a></p>"
        f"<h2>Paired devices</h2><table><tr><th>Name</th><th>ID</th><th>State</th><th>Last seen (UTC)</th><th></th></tr>{device_rows}</table>"
        f"<h2>Status</h2><p class=muted>Sampling now: {e(', '.join(ctx.hub.running_topics) or 'nothing (no phone is watching)')}<br>"
        f"Not available on this PC: {e(unsupported)}<br>Allowlisted actions: {len(ctx.actions.all())}</p>"
        f"<h2>Recent activity</h2><table><tr><th>Time (UTC)</th><th>Event</th><th>Address</th><th>Detail</th></tr>{log_rows}</table>"
    )


@router.get("/pair", response_class=HTMLResponse)
async def pair_page(ctx: Ctx) -> HTMLResponse:
    with ctx.db() as db:
        code = create_pairing_code(db, ctx.config.security.pairing_code_ttl_s)
    ctx.audit("pairing_code_created", ip="127.0.0.1")
    shown = format_code(code)
    url = f"http://{lan_ip()}:{ctx.config.server.lan_port}/"
    out = io.BytesIO()
    segno.make(f"{url}#pair={code}", error="m").save(out, kind="svg", scale=6, border=1, xmldecl=False)
    minutes = ctx.config.security.pairing_code_ttl_s // 60
    return _page(
        "<h1>Pair a phone</h1><p class=muted>The phone must be on the same Wi-Fi network as this PC.</p>"
        f"<div class='card row'><div class=qr>{out.getvalue().decode()}</div><div>"
        f"<div class=muted>Pairing code</div><div class=code>{html.escape(shown)}</div>"
        f"<div class=muted>Valid for {minutes} minutes, one use.</div>"
        f"<p>Panel address<br><code>{html.escape(url)}</code></p></div></div>"
        "<ol><li>Scan the QR code with the iPhone Camera app and open the link in Safari.</li>"
        "<li>Tap <b>Share</b> → <b>Add to Home Screen</b>, then open Phoney from the Home Screen.</li>"
        "<li>Type the pairing code shown here.</li></ol>"
        "<p class=muted>If the page does not load on the phone, allow the agent through Windows Firewall "
        "for <b>Private</b> networks and check that your Wi-Fi is set to Private.</p>"
        "<p><a href='/'>Back to status</a> · <a href='/pair'>New code</a></p>"
    )


@router.post("/local/devices/{device_id}/revoke")
async def revoke(device_id: str, ctx: Ctx) -> RedirectResponse:
    if not await revoke_device(ctx, device_id, "127.0.0.1"):
        raise ApiError(404, "not_found", "Device not found.")
    return RedirectResponse("/", status_code=303)


@router.post("/local/hooks/{tool}")
async def hook(tool: str, request: Request, ctx: Ctx) -> dict[str, Any]:
    """Receives a hook event from an AI coding tool. Always answers 200 with an empty object so the
    tool carries on untouched: this endpoint observes, it never steers."""
    enabled = {"claude-code": ctx.config.integrations.claude_code, "codex": ctx.config.integrations.codex}
    if tool not in TOOLS:
        raise ApiError(404, "not_found", "Unknown tool.")
    raw = await request.body()
    if not enabled[tool] or len(raw) > MAX_BODY_BYTES * 4:
        return {}
    try:
        payload = await request.json()
    except ValueError:
        return {}
    if isinstance(payload, dict):
        ctx.ai.ingest(tool, payload)
    return {}


@router.post("/local/hooks/claude-code/permission")
async def permission_hook(request: Request, ctx: Ctx) -> dict[str, Any]:
    """Claude Code is about to ask for permission. Offer the question to the phone and wait.

    An empty answer means "no decision", and Claude Code then asks in the terminal as usual. That is
    what happens when the feature is off, no phone is listening, or nobody answers in time.
    """
    on = ctx.config.integrations
    try:
        payload = await request.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "PermissionRequest":
        return {}
    if not (on.claude_code and on.claude_permissions) or not ctx.hub.has_subscribers("ai.permissions"):
        ctx.ai.ingest("claude-code", payload)  # still shows the session as needing attention
        return {}
    pending = ctx.permissions.open("claude-code", payload, on.permission_wait_s)
    if pending is None:
        return {}
    decision = await ctx.permissions.wait(pending, on.permission_wait_s)
    ctx.audit("permission", ok=decision == "allow", detail=f"{decision or 'unanswered'}: {pending.tool_name}")
    return hook_response(decision)


@router.post("/local/statusline")
async def statusline(request: Request, ctx: Ctx) -> dict[str, Any]:
    """Receives rate-limit numbers from the Claude Code status-line forwarder. Display data only."""
    raw = await request.body()
    if not ctx.config.integrations.claude_code or len(raw) > MAX_BODY_BYTES:
        return {}
    try:
        payload = await request.json()
    except ValueError:
        return {}
    if isinstance(payload, dict) and ctx.ai.set_usage(payload):
        ctx.hub.publish("ai.usage", ctx.ai.usage(), kind="data")
    return {}


def create_local_app(ctx: AgentContext) -> FastAPI:
    app = FastAPI(title="Phoney local", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    install_error_handlers(app)
    app.include_router(router)
    app.add_middleware(LocalOnlyMiddleware, port=ctx.config.server.local_port)
    return app
