"""Builds the two applications and runs them in one event loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app import __version__
from app.api import extras, lan
from app.api.deps import GuardMiddleware
from app.api.local import create_local_app, lan_ip
from app.core.config import default_data_dir
from app.core.context import AgentContext, build_context
from app.core.errors import install_error_handlers
from app.networking import ws
from app.tray import start_tray

WEB_DIR = Path(__file__).resolve().parent / "web"
log = logging.getLogger("phoney")


class PanelFiles(StaticFiles):
    """The phone panel. Revalidated on every load so an agent update reaches the phone immediately."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'"
        )
        return response


def create_lan_app(ctx: AgentContext) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        for connection in list(ctx.connections.values()):
            await connection.close(1001)
        await ctx.hub.close()

    app = FastAPI(title="Phoney agent", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    install_error_handlers(app)
    app.include_router(lan.router, prefix="/api/v1")
    app.include_router(extras.router, prefix="/api/v1")
    app.include_router(ws.router, prefix="/api/v1")
    app.mount("/", PanelFiles(directory=WEB_DIR, html=True), name="panel")
    app.add_middleware(GuardMiddleware, ctx=ctx)
    lan.seed_default_deck(ctx)
    return app


def setup_logging(data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "agent.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        # Started without a console (pythonw, autostart) there is no stderr to write to.
        handlers=[file_handler, *([logging.StreamHandler()] if sys.stderr else [])],
    )


async def serve(ctx: AgentContext) -> None:
    server_cfg = ctx.config.server
    common = {"log_level": "warning", "access_log": False, "log_config": None}  # use our logging setup
    lan_server = uvicorn.Server(
        uvicorn.Config(
            create_lan_app(ctx),
            host=server_cfg.lan_host,
            port=server_cfg.lan_port,
            ws="websockets-sansio",
            ws_max_size=16 * 1024,
            **common,
        )  # fmt: skip
    )
    local_server = uvicorn.Server(
        uvicorn.Config(create_local_app(ctx), host="127.0.0.1", port=server_cfg.local_port, **common)
    )
    log.info("Phoney agent %s", __version__)
    log.info("Pair a phone and see status:  http://127.0.0.1:%s", server_cfg.local_port)
    log.info("Panel address for the phone:  http://%s:%s", lan_ip(), server_cfg.lan_port)

    def stop() -> None:
        lan_server.should_exit = local_server.should_exit = True

    tray = start_tray(ctx, stop)  # None when the optional tray packages are not installed
    tasks = [asyncio.create_task(lan_server.serve()), asyncio.create_task(local_server.serve())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        stop()  # one stopped (Ctrl+C, tray Quit, or a bind error): stop both
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if tray is not None:
            tray.stop()


def run() -> None:
    data_dir = default_data_dir()
    setup_logging(data_dir)
    ctx = build_context(data_dir)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(ctx))
